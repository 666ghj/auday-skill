#requires -Version 5.1
# Windows system-only entrypoint. Run with a process-scoped ExecutionPolicy Bypass;
# this script never changes a machine/user policy or installs global tools.
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

function Write-AudayProgress([string] $Message) {
    [Console]::Error.WriteLine('[Auday] ' + $Message)
}

function ConvertTo-AudayNativeArgument([string] $Value) {
    # CommandLineToArgvW quoting, including embedded quotes and trailing slashes.
    $builder = New-Object System.Text.StringBuilder
    [void] $builder.Append('"')
    $slashes = 0
    foreach ($character in $Value.ToCharArray()) {
        if ($character -eq '\') { $slashes++; continue }
        if ($character -eq '"') {
            [void] $builder.Append(('\' * ($slashes * 2 + 1)))
        } else {
            [void] $builder.Append(('\' * $slashes))
        }
        $slashes = 0
        [void] $builder.Append($character)
    }
    [void] $builder.Append(('\' * ($slashes * 2)))
    [void] $builder.Append('"')
    return $builder.ToString()
}

function Get-AudayPrivateEnvironment([string] $Root) {
    $environment = @{}
    foreach ($entry in [Environment]::GetEnvironmentVariables().GetEnumerator()) {
        if ($entry.Key -match '^(UV_|PIP_)' -or $entry.Key -in @(
            'PYTHONPATH', 'PYTHONHOME', 'PYTHONSTARTUP', 'VIRTUAL_ENV', 'CONDA_PREFIX')) { continue }
        $environment[$entry.Key] = $entry.Value
    }
    $environment['UV_NO_CONFIG'] = '1'
    $environment['UV_CACHE_DIR'] = Join-Path $Root 'cache\uv'
    $environment['UV_PYTHON_INSTALL_DIR'] = Join-Path $Root 'runtime\python'
    $environment['UV_PYTHON'] = '3.13.16'
    $environment['UV_MANAGED_PYTHON'] = '1'
    $environment['PYTHONNOUSERSITE'] = '1'
    $environment['PYTHONUTF8'] = '1'
    $environment['PYTHONIOENCODING'] = 'utf-8'
    $environment['HF_HOME'] = Join-Path $Root 'cache\huggingface'
    $environment['TORCH_HOME'] = Join-Path $Root 'cache\torch'
    return $environment
}

function Invoke-AudayProcess {
    param([string] $Executable, [string[]] $Arguments, [hashtable] $Environment,
          [int] $TimeoutSeconds = 600, [switch] $Capture)
    $start = New-Object System.Diagnostics.ProcessStartInfo
    $start.FileName = $Executable
    $start.Arguments = (@($Arguments | ForEach-Object { ConvertTo-AudayNativeArgument $_ }) -join ' ')
    $start.UseShellExecute = $false
    # Keep stderr/stdin connected to the terminal for progress and local prompts.
    $start.RedirectStandardOutput = $Capture.IsPresent
    if ($Capture) { $start.StandardOutputEncoding = New-Object Text.UTF8Encoding($false) }
    $start.EnvironmentVariables.Clear()
    foreach ($key in $Environment.Keys) { $start.EnvironmentVariables[$key] = $Environment[$key] }
    $process = New-Object System.Diagnostics.Process
    $process.StartInfo = $start
    try {
        [void] $process.Start()
        $read = $null
        if ($Capture) { $read = $process.StandardOutput.ReadToEndAsync() }
        if ($TimeoutSeconds -gt 0) {
            if (-not $process.WaitForExit($TimeoutSeconds * 1000)) {
                $process.Kill()
                $process.WaitForExit()
                throw 'private_runtime_process_timed_out'
            }
        } else { $process.WaitForExit() }
        $output = ''
        if ($Capture) { $output = $read.GetAwaiter().GetResult() }
        return [pscustomobject] @{ ExitCode = $process.ExitCode; Output = $output }
    } finally { $process.Dispose() }
}

function Assert-AudayOrdinaryPath([string] $Path) {
    $current = [IO.Path]::GetFullPath($Path)
    while ($current) {
        try { $attributes = [IO.File]::GetAttributes($current) }
        catch {
            $cause = $_.Exception.InnerException
            if ($cause -isnot [IO.FileNotFoundException] -and $cause -isnot [IO.DirectoryNotFoundException]) { throw }
            $attributes = 0
        }
        if (($attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
            throw 'private_runtime_path_is_reparse_point'
        }
        $current = [IO.Path]::GetDirectoryName($current)
    }
}

function Get-AudayVerifiedDownload {
    param([string] $Url, [string] $Destination, [string] $Sha256, [long] $Size)
    if ($Url -notmatch '^https://' -or $Sha256 -cnotmatch '^[0-9a-f]{64}$' -or
            $Size -le 0 -or $Size -gt 21474836480) { throw 'invalid_runtime_manifest' }
    Assert-AudayOrdinaryPath $Destination
    $partial = $Destination + '.partial'
    Assert-AudayOrdinaryPath $partial
    if ([IO.File]::Exists($Destination)) {
        if ((Get-Item -LiteralPath $Destination).Length -ne $Size -or
                (Get-FileHash -LiteralPath $Destination -Algorithm SHA256).Hash.ToLowerInvariant() -ne $Sha256) {
            throw 'existing_component_hash_mismatch: preserve it and use a new setup root'
        }
        return $Destination
    }
    [void] [IO.Directory]::CreateDirectory([IO.Path]::GetDirectoryName($Destination))
    $oldProtocol = [Net.ServicePointManager]::SecurityProtocol
    [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
    try {
        for ($attempt = 0; $attempt -lt 3; $attempt++) {
            $response = $null; $inputStream = $null; $outputStream = $null
            try {
                Write-AudayProgress 'Downloading private setup runtime'
                $watch = [Diagnostics.Stopwatch]::StartNew()
                $currentUrl = [Uri] $Url
                for ($redirects = 0; ; $redirects++) {
                    if ($currentUrl.Scheme -ne 'https' -or $redirects -gt 5) {
                        throw 'insecure_or_excessive_download_redirect'
                    }
                    $request = [Net.HttpWebRequest]::Create($currentUrl)
                    $request.AllowAutoRedirect = $false
                    $request.Timeout = 60000
                    $request.ReadWriteTimeout = 60000
                    $request.UserAgent = 'Auday-Skill-Setup'
                    $request.Headers['Accept-Encoding'] = 'identity'
                    $response = $request.GetResponse()
                    $status = [int] $response.StatusCode
                    if ($status -in @(301, 302, 303, 307, 308)) {
                        $nextUrl = New-Object Uri($currentUrl, $response.Headers['Location'])
                        $response.Dispose(); $response = $null
                        $currentUrl = $nextUrl
                        continue
                    }
                    if ($status -ne 200) { throw 'unexpected_download_response' }
                    break
                }
                if ($response.ContentLength -gt 0 -and $response.ContentLength -ne $Size) {
                    throw 'download_size_mismatch'
                }
                $inputStream = $response.GetResponseStream()
                $outputStream = New-Object IO.FileStream($partial, [IO.FileMode]::Create, [IO.FileAccess]::Write, [IO.FileShare]::None)
                $buffer = New-Object byte[] 1048576
                [long] $received = 0
                while (($readCount = $inputStream.Read($buffer, 0, $buffer.Length)) -gt 0) {
                    $received += $readCount
                    if ($received -gt $Size) { throw 'download_exceeds_expected_size' }
                    if ($watch.Elapsed.TotalSeconds -gt 600) { throw 'download_timed_out' }
                    $outputStream.Write($buffer, 0, $readCount)
                }
                $outputStream.Flush($true)
                if ($received -ne $Size) { throw 'incomplete_download' }
                break
            } catch {
                if ($attempt -eq 2 -or $_.Exception.Message -match '^(insecure_|download_(size_mismatch|exceeds_expected_size))') { throw }
                Write-AudayProgress 'Download interrupted; retrying the bounded runtime download'
                Start-Sleep -Seconds 1
            } finally {
                if ($outputStream) { $outputStream.Dispose() }
                if ($inputStream) { $inputStream.Dispose() }
                if ($response) { $response.Dispose() }
            }
        }
    } finally { [Net.ServicePointManager]::SecurityProtocol = $oldProtocol }
    Write-AudayProgress 'Verifying downloaded component'
    if ((Get-FileHash -LiteralPath $partial -Algorithm SHA256).Hash.ToLowerInvariant() -ne $Sha256) {
        [IO.File]::Move($partial, $partial + '.corrupt-' + [Guid]::NewGuid().ToString('N'))
        throw 'download_hash_mismatch: invalid bytes retained separately'
    }
    [IO.File]::Move($partial, $Destination)
    return $Destination
}

function Expand-AudaySafeZip([string] $Archive, [string] $Destination) {
    Add-Type -AssemblyName System.IO.Compression.FileSystem
    if ([IO.Directory]::Exists($Destination) -or [IO.File]::Exists($Destination)) {
        throw 'extraction_destination_exists'
    }
    Assert-AudayOrdinaryPath $Destination
    [void] [IO.Directory]::CreateDirectory($Destination)
    $zip = $null
    try {
        $zip = [IO.Compression.ZipFile]::OpenRead($Archive)
        $targets = New-Object 'System.Collections.Generic.Dictionary[string,object]' ([StringComparer]::OrdinalIgnoreCase)
        [long] $total = 0
        if ($zip.Entries.Count -gt 100000) { throw 'archive_exceeds_expected_size' }
        foreach ($entry in $zip.Entries) {
            $name = $entry.FullName
            if (-not $name -or $name.StartsWith('/') -or $name.Contains('\')) { throw 'unsafe_archive_path' }
            $parts = $name.TrimEnd('/').Split('/')
            foreach ($part in $parts) {
                if (-not $part -or $part -in @('.', '..') -or $part.EndsWith('.') -or $part.EndsWith(' ') -or
                        $part -match '[<>:"|?*\x00-\x1f]' -or $part -match '^(con|prn|aux|nul|com[1-9\u00b9\u00b2\u00b3]|lpt[1-9\u00b9\u00b2\u00b3])(?:\..*)?$') {
                    throw 'unsafe_archive_path'
                }
            }
            $kind = ($entry.ExternalAttributes -shr 16) -band 61440
            if ($kind -notin @(0, 16384, 32768) -or ($entry.ExternalAttributes -band 1024) -ne 0) {
                throw 'unsupported_archive_member'
            }
            $key = $name.TrimEnd('/')
            if ($targets.ContainsKey($key)) { throw 'duplicate_archive_path' }
            $targets.Add($key, $entry)
            $total += $entry.Length
            if ($total -gt 21474836480) { throw 'archive_exceeds_expected_size' }
        }
        foreach ($key in $targets.Keys) {
            $parent = $key
            while ($parent.Contains('/')) {
                $parent = $parent.Substring(0, $parent.LastIndexOf('/'))
                if ($targets.ContainsKey($parent) -and -not $targets[$parent].FullName.EndsWith('/')) { throw 'archive_file_parent' }
            }
        }
        foreach ($entry in $zip.Entries) {
            $target = Join-Path $Destination ($entry.FullName.Replace('/', [IO.Path]::DirectorySeparatorChar))
            if ($entry.FullName.EndsWith('/')) { [void] [IO.Directory]::CreateDirectory($target); continue }
            [void] [IO.Directory]::CreateDirectory([IO.Path]::GetDirectoryName($target))
            $source = $null; $output = $null
            try {
                $source = $entry.Open()
                $output = New-Object IO.FileStream($target, [IO.FileMode]::CreateNew, [IO.FileAccess]::Write, [IO.FileShare]::None)
                $buffer = New-Object byte[] 1048576
                [long] $remaining = $entry.Length
                while ($remaining -gt 0) {
                    $count = $source.Read($buffer, 0, [int] [Math]::Min($buffer.Length, $remaining))
                    if ($count -le 0) { throw 'incomplete_archive_member' }
                    $output.Write($buffer, 0, $count)
                    $remaining -= $count
                }
                if ($source.ReadByte() -ne -1) { throw 'archive_exceeds_expected_size' }
            } finally {
                if ($output) { $output.Dispose() }
                if ($source) { $source.Dispose() }
            }
        }
    } catch {
        if ($zip) { $zip.Dispose(); $zip = $null }
        [IO.Directory]::Delete($Destination, $true)
        throw
    } finally { if ($zip) { $zip.Dispose() } }
}

function Start-AudayBootstrap([string[]] $CommandArguments) {
    if ([Environment]::OSVersion.Platform -ne [PlatformID]::Win32NT) { throw 'windows_required' }
    if ([Environment]::OSVersion.Version.Major -lt 10) { throw 'requires_windows_10_or_later' }
    $architecture = $env:PROCESSOR_ARCHITECTURE
    if ($env:PROCESSOR_ARCHITEW6432) { $architecture = $env:PROCESSOR_ARCHITEW6432 }
    if ($architecture -ne 'AMD64') { throw 'windows_x64_required' }
    $localData = $env:LOCALAPPDATA
    if (-not $localData) { $localData = [Environment]::GetFolderPath('LocalApplicationData') }
    $dataRoot = $null
    if ($localData) { $dataRoot = Join-Path $localData 'AudayWorkflow' }
    $forward = New-Object 'System.Collections.Generic.List[string]'
    $index = 0
    while ($index -lt $CommandArguments.Count) {
        $option = $CommandArguments[$index]
        if ($option -notin @('--home', '--profile')) { break }
        if ($index + 1 -ge $CommandArguments.Count) { throw ($option + '_needs_a_value') }
        if ($option -eq '--home') { $dataRoot = $CommandArguments[$index + 1] }
        else { $forward.Add($option); $forward.Add($CommandArguments[$index + 1]) }
        $index += 2
    }
    if (-not $dataRoot) { throw 'local_application_data_directory_unavailable' }
    if ($dataRoot -notmatch '^[a-zA-Z]:[\\/]' -and $dataRoot -notmatch '^\\\\[^\\]+\\[^\\]+') { throw 'home_must_be_an_absolute_directory_path' }
    $dataRoot = [IO.Path]::GetFullPath($dataRoot)
    if ($index -lt $CommandArguments.Count -and $CommandArguments[$index] -eq 'check') {
        if ($index + 1 -ne $CommandArguments.Count) { throw 'check_takes_no_arguments' }
        $memory = $null; $free = $null
        try { $memory = [long] (Get-CimInstance Win32_ComputerSystem -OperationTimeoutSec 10).TotalPhysicalMemory } catch { }
        try { $free = (New-Object IO.DriveInfo([IO.Path]::GetPathRoot($dataRoot))).AvailableFreeSpace } catch { }
        [Console]::Out.WriteLine((@{ ok = $true; windows = [Environment]::OSVersion.Version.ToString(); architecture = 'x86_64';
            memoryBytes = $memory; availableDiskBytes = $free; downloadsStarted = $false;
            nextStep = 'run bootstrap doctor to prepare the private base runtime' } | ConvertTo-Json -Compress))
        return 0
    }
    if ($index -eq $CommandArguments.Count) { $forward.Add('setup') }
    while ($index -lt $CommandArguments.Count) { $forward.Add($CommandArguments[$index]); $index++ }
    Assert-AudayOrdinaryPath $dataRoot
    [void] [IO.Directory]::CreateDirectory($dataRoot)
    $assets = @(Import-Csv -LiteralPath (Join-Path $PSScriptRoot 'runtime-assets.tsv') -Delimiter "`t" | Where-Object {
        $_.component -eq 'uv' -and $_.architecture -eq 'x86_64' -and $_.platform -eq 'windows'
    })
    if ($assets.Count -ne 1) { throw 'unsupported_runtime_asset' }
    $asset = $assets[0]
    foreach ($name in @('install_dir', 'executable')) {
        if ($asset.$name -cnotmatch '^[A-Za-z0-9_.-]+$' -or $asset.$name -in @('.', '..')) { throw 'invalid_runtime_manifest' }
    }
    if ($asset.archive_root -ne '.' -or $asset.archive_format -ne 'zip') { throw 'invalid_runtime_manifest' }
    $uvDirectory = Join-Path $dataRoot ('runtime\' + $asset.install_dir)
    $uv = Join-Path $uvDirectory $asset.executable
    $marker = Join-Path $uvDirectory '.auday-archive-sha256'
    Assert-AudayOrdinaryPath $uvDirectory
    if ([IO.Directory]::Exists($uvDirectory)) {
        if (-not [IO.File]::Exists($marker) -or ([IO.File]::ReadAllText($marker).Trim() -cne $asset.sha256) -or -not [IO.File]::Exists($uv)) {
            throw 'existing_runtime_incomplete: preserve it and use a new setup root'
        }
    } else {
        $archive = Get-AudayVerifiedDownload $asset.url (Join-Path $dataRoot ('cache\downloads\' + $asset.sha256 + '.zip')) $asset.sha256 ([long] $asset.size)
        [void] [IO.Directory]::CreateDirectory((Join-Path $dataRoot 'runtime'))
        $stage = Join-Path $dataRoot ('runtime\.bootstrap-' + [Guid]::NewGuid().ToString('N'))
        try {
            Expand-AudaySafeZip $archive $stage
            if (-not [IO.File]::Exists((Join-Path $stage $asset.executable))) { throw 'runtime_executable_missing_from_archive' }
            [IO.File]::WriteAllText((Join-Path $stage '.auday-archive-sha256'), $asset.sha256 + "`n")
            [IO.Directory]::Move($stage, $uvDirectory)
        } finally { if ([IO.Directory]::Exists($stage)) { [IO.Directory]::Delete($stage, $true) } }
    }
    $environment = Get-AudayPrivateEnvironment $dataRoot
    $found = Invoke-AudayProcess $uv @('--no-config', 'python', 'find', '--managed-python', 'cpython@3.13.16') $environment -Capture -TimeoutSeconds 60
    if ($found.ExitCode -ne 0) {
        Write-AudayProgress 'Preparing private Python runtime'
        $installed = Invoke-AudayProcess $uv @('--no-config', 'python', 'install', 'cpython@3.13.16', '--no-bin') $environment
        if ($installed.ExitCode -ne 0) { return $installed.ExitCode }
        $found = Invoke-AudayProcess $uv @('--no-config', 'python', 'find', '--managed-python', 'cpython@3.13.16') $environment -Capture -TimeoutSeconds 60
        if ($found.ExitCode -ne 0) { return $found.ExitCode }
    }
    $python = $found.Output.Trim()
    $privatePython = [IO.Path]::GetFullPath((Join-Path $dataRoot 'runtime\python')).TrimEnd('\') + '\'
    if (-not [IO.File]::Exists($python) -or -not [IO.Path]::GetFullPath($python).StartsWith($privatePython, [StringComparison]::OrdinalIgnoreCase)) {
        throw 'python_runtime_outside_private_setup'
    }
    Assert-AudayOrdinaryPath $python
    $probe = "import ssl,sqlite3,sys; assert sys.version_info[:3] == (3,13,16); assert ssl.HAS_PSK; assert hasattr(ssl.SSLContext, 'set_psk_client_callback')"
    $checked = Invoke-AudayProcess $python @('-I', '-c', $probe) $environment -TimeoutSeconds 30
    if ($checked.ExitCode -ne 0) { return $checked.ExitCode }
    $cliArguments = @((Join-Path $PSScriptRoot 'bootstrap_windows.py'), '--home', $dataRoot) + $forward.ToArray()
    $finished = Invoke-AudayProcess $python $cliArguments $environment -TimeoutSeconds 0
    return $finished.ExitCode
}

if ($MyInvocation.InvocationName -ne '.') {
    try {
        [Console]::OutputEncoding = New-Object Text.UTF8Encoding($false)
        exit (Start-AudayBootstrap $args)
    } catch {
        [Console]::Out.WriteLine((@{ ok = $false; error = $_.Exception.Message } | ConvertTo-Json -Compress))
        exit 1
    }
}
