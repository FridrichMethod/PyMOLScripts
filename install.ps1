# Install the PyMOL startup files configs/.pymolrc and configs/.pymolrc.py as
# DIR\.pymolrc and DIR\.pymolrc.py, where DIR is -Prefix or %USERPROFILE%
# ($HOME on Linux and macOS). Native Windows counterpart of install.sh; runs on
# PowerShell 7 (pwsh) and Windows PowerShell 5.1.
#
#   pwsh -NoProfile -File install.ps1 [-Prefix DIR]
#
# Source: the configs\ directory next to this script when it holds both files,
# otherwise both are downloaded with Invoke-WebRequest from
#   $env:PYMOLSCRIPTS_BASE_URL (default https://raw.githubusercontent.com/FridrichMethod/PyMOLScripts)
#   /$env:PYMOLSCRIPTS_REF (default main)/configs/<name>
#
# The same command installs and updates; there is no state file. A file whose
# installed copy is already a regular file with identical content is left
# alone, so a run with nothing to change writes nothing. Changed files are
# written to a temporary directory inside DIR and renamed into place, which
# replaces a symlink (dangling or not) instead of writing through it, and
# nothing is renamed unless both files were fetched.
#
# Exit status: 0 success (including "already current"), 1 error, 2 usage error.
#
# Arguments are parsed from $args rather than a param() block so that an
# unknown or incomplete argument is a usage error (exit 2) instead of being
# ignored or reported by the parameter binder.

Set-StrictMode -Version 3.0
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'

$Prog = 'install.ps1'
$Names = @('.pymolrc', '.pymolrc.py')
$DefaultBaseUrl = 'https://raw.githubusercontent.com/FridrichMethod/PyMOLScripts'
$OnWindows = ($PSVersionTable.PSEdition -eq 'Desktop') -or
    ((Get-Variable -Name IsWindows -ValueOnly -ErrorAction Ignore) -eq $true)
$HomeVariable = if ($OnWindows) { 'USERPROFILE' } else { 'HOME' }
$HomeShown = if ($OnWindows) { '%USERPROFILE%' } else { '$HOME' }

function Write-Usage {
    @(
        'Usage: install.ps1 [-Prefix DIR]'
        ''
        "Install PyMOLScripts' .pymolrc and .pymolrc.py into DIR (default: $HomeShown)."
        'Rerun the same command to update; files that are already current are left'
        'untouched.'
        ''
        'Options:'
        "  -Prefix DIR  absolute directory to install into (default: $HomeShown)"
        '  -Help        show this help and exit'
        ''
        'Environment:'
        '  PYMOLSCRIPTS_REF       branch, tag or commit to download (default: main)'
        '  PYMOLSCRIPTS_BASE_URL  download base URL'
        "                         (default: $DefaultBaseUrl)"
    ) | ForEach-Object { [Console]::Out.WriteLine($_) }
}

function Write-Status([string]$Message) {
    [Console]::Out.WriteLine("PyMOLScripts: $Message")
}

function Exit-Error([string]$Message) {
    [Console]::Error.WriteLine("${Prog}: error: $Message")
    exit 1
}

function Exit-Usage([string]$Message) {
    [Console]::Error.WriteLine("${Prog}: $Message")
    [Console]::Error.WriteLine("Try '$Prog -Help'.")
    exit 2
}

function Test-AbsolutePath([string]$Path) {
    if ($OnWindows) {
        # Drive-qualified (C:\x, C:/x) or UNC (\\server\share, \\?\C:\x).
        return ($Path -match '^[A-Za-z]:[\\/]') -or ($Path -match '^[\\/]{2}[^\\/]')
    }
    return $Path.StartsWith('/')
}

# A file that is not a directory and not a symlink or junction (dangling or
# not). Other reparse points, such as OneDrive placeholders, are ordinary files.
function Test-RegularFile([string]$Path) {
    $item = Get-Item -LiteralPath $Path -Force -ErrorAction Ignore
    if ($null -eq $item -or $item.PSIsContainer) { return $false }
    return @('SymbolicLink', 'Junction') -notcontains [string]$item.LinkType
}

# A directory, a link to one, or a directory-type link whose target is gone:
# entries a file rename cannot replace.
function Test-DirectoryEntry([string]$Path) {
    if ([IO.Directory]::Exists($Path)) { return $true }
    $item = Get-Item -LiteralPath $Path -Force -ErrorAction Ignore
    return $null -ne $item -and [bool]($item.Attributes -band [IO.FileAttributes]::Directory)
}

function Test-SameBytes([string]$A, [string]$B) {
    $x = [IO.File]::ReadAllBytes($A)
    $y = [IO.File]::ReadAllBytes($B)
    if ($x.Length -ne $y.Length) { return $false }
    for ($i = 0; $i -lt $x.Length; $i++) {
        if ($x[$i] -ne $y[$i]) { return $false }
    }
    return $true
}

# Rename $From over $To. The overwrite overload of File.Move (.NET Core 3.0+,
# so PowerShell 7) renames over the destination entry itself, replacing a
# symlink instead of following it. Windows PowerShell 5.1 lacks it: there a
# regular file is swapped with File.Replace, and a link is deleted first
# (File.Delete removes the link, not its target).
function Move-Replace([string]$From, [string]$To) {
    $overwriteMove = [IO.File].GetMethod('Move', [Type[]]@([string], [string], [bool]))
    if ($null -ne $overwriteMove) {
        [IO.File]::Move($From, $To, $true)
    } elseif (Test-RegularFile $To) {
        [IO.File]::Replace($From, $To, $null)
    } else {
        [IO.File]::Delete($To)
        [IO.File]::Move($From, $To)
    }
}

function Get-Message($ErrorRecord) {
    return ($ErrorRecord.Exception.Message -replace '\s+', ' ').Trim().TrimEnd('.')
}

# --- arguments ---------------------------------------------------------------

$Prefix = $null
$i = 0
while ($i -lt $args.Count) {
    $arg = [string]$args[$i]
    if ($arg -match '^(-|--)prefix$') {
        if ($i + 1 -ge $args.Count) { Exit-Usage '-Prefix needs a directory' }
        $Prefix = [string]$args[$i + 1]
        $i += 2
        continue
    }
    if ($arg -match '^--?prefix[=:](.*)$') {
        $Prefix = $Matches[1]
        $i += 1
        continue
    }
    if ($arg -match '^(-h|-help|--help|-\?)$') {
        Write-Usage
        exit 0
    }
    if ($arg.StartsWith('-')) { Exit-Usage "unknown option: $arg" }
    Exit-Usage "unexpected argument: $arg"
}

if ($null -ne $Prefix) {
    if (-not (Test-AbsolutePath $Prefix)) {
        Exit-Usage "-Prefix must be an absolute path: '$Prefix'"
    }
    $Dir = $Prefix
} else {
    $Dir = [Environment]::GetEnvironmentVariable($HomeVariable)
    if ([string]::IsNullOrEmpty($Dir)) {
        Exit-Error "$HomeVariable is not set; pass -Prefix DIR"
    }
    if (-not (Test-AbsolutePath $Dir)) {
        Exit-Error "$HomeVariable is not an absolute path: $Dir; pass -Prefix DIR"
    }
}

$Stage = $null # download staging directory under the system temp directory
$Work = $null  # temporary directory inside $Dir, created only when a file changes
try {
    try {
        # --- preflight: refuse what a rename cannot replace ------------------
        if ([IO.File]::Exists($Dir) -and -not [IO.Directory]::Exists($Dir)) {
            Exit-Error "$Dir exists and is not a directory"
        }
        foreach ($name in $Names) {
            $dest = [IO.Path]::Combine($Dir, $name)
            if (Test-DirectoryEntry $dest) {
                Exit-Error "$dest is a directory or a link to one; remove it and rerun"
            }
        }

        # --- source ------------------------------------------------------------
        $Src = $null
        if ($PSScriptRoot) {
            $checkout = [IO.Path]::Combine($PSScriptRoot, 'configs')
            if ([IO.File]::Exists([IO.Path]::Combine($checkout, '.pymolrc')) -and
                [IO.File]::Exists([IO.Path]::Combine($checkout, '.pymolrc.py'))) {
                $Src = $checkout
            }
        }
        if ($null -eq $Src) {
            if ($PSVersionTable.PSEdition -eq 'Desktop') {
                # raw.githubusercontent.com needs TLS 1.2, which .NET Framework
                # may not offer by default.
                [Net.ServicePointManager]::SecurityProtocol =
                    [Net.ServicePointManager]::SecurityProtocol -bor [Net.SecurityProtocolType]::Tls12
            }
            $baseUrl = $env:PYMOLSCRIPTS_BASE_URL
            if ([string]::IsNullOrEmpty($baseUrl)) { $baseUrl = $DefaultBaseUrl }
            $baseUrl = $baseUrl.TrimEnd('/')
            $ref = $env:PYMOLSCRIPTS_REF
            if ([string]::IsNullOrEmpty($ref)) { $ref = 'main' }
            $tmpRoot = [IO.Path]::GetTempPath()
            try {
                $Stage = [IO.Path]::Combine($tmpRoot, 'pymolscripts-install.' + [IO.Path]::GetRandomFileName())
                [void][IO.Directory]::CreateDirectory($Stage)
            } catch {
                Exit-Error "cannot create a temporary directory in ${tmpRoot}: $(Get-Message $_)"
            }
            foreach ($name in $Names) {
                $url = "$baseUrl/$ref/configs/$name"
                $out = [IO.Path]::Combine($Stage, $name)
                try {
                    Invoke-WebRequest -Uri $url -OutFile $out -UseBasicParsing -TimeoutSec 300
                } catch {
                    Exit-Error "download failed ($(Get-Message $_)): $url"
                }
                if (-not [IO.File]::Exists($out) -or (New-Object IO.FileInfo $out).Length -eq 0) {
                    Exit-Error "downloaded file is empty: $url"
                }
            }
            $Src = $Stage
        }

        # --- compare, stage, rename --------------------------------------------
        $changed = @()
        foreach ($name in $Names) {
            $dest = [IO.Path]::Combine($Dir, $name)
            if ((Test-RegularFile $dest) -and (Test-SameBytes ([IO.Path]::Combine($Src, $name)) $dest)) {
                continue
            }
            $changed += $name
        }
        if ($changed.Count -eq 0) {
            Write-Status ".pymolrc and .pymolrc.py in $Dir are already current"
            exit 0
        }

        if (-not [IO.Directory]::Exists($Dir)) {
            try {
                [void][IO.Directory]::CreateDirectory($Dir)
            } catch {
                Exit-Error "cannot create ${Dir}: $(Get-Message $_)"
            }
        }
        try {
            $Work = [IO.Path]::Combine($Dir, '.pymolscripts-install.' + [IO.Path]::GetRandomFileName())
            [void][IO.Directory]::CreateDirectory($Work)
        } catch {
            Exit-Error "cannot create a temporary directory in ${Dir}: $(Get-Message $_)"
        }
        foreach ($name in $changed) {
            $tmp = [IO.Path]::Combine($Work, $name)
            try {
                [IO.File]::WriteAllBytes($tmp, [IO.File]::ReadAllBytes([IO.Path]::Combine($Src, $name)))
            } catch {
                Exit-Error "cannot write ${tmp}: $(Get-Message $_)"
            }
        }
        foreach ($name in $changed) {
            $dest = [IO.Path]::Combine($Dir, $name)
            $verb = if (Test-RegularFile $dest) { 'updated' } else { 'installed' }
            if (Test-DirectoryEntry $dest) {
                Exit-Error "$dest is a directory or a link to one; remove it and rerun"
            }
            try {
                Move-Replace ([IO.Path]::Combine($Work, $name)) $dest
            } catch {
                Exit-Error "cannot rename into ${dest}: $(Get-Message $_)"
            }
            Write-Status "$verb $dest"
        }
    } catch {
        Exit-Error (Get-Message $_)
    }
} finally {
    foreach ($tmpDir in @($Work, $Stage)) {
        if ($tmpDir -and [IO.Directory]::Exists($tmpDir)) {
            try {
                [IO.Directory]::Delete($tmpDir, $true)
            } catch {
                [Console]::Error.WriteLine("${Prog}: warning: cannot remove ${tmpDir}: $(Get-Message $_)")
            }
        }
    }
}
exit 0
