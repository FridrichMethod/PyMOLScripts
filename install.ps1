# Install the PyMOL startup files configs/.pymolrc and configs/.pymolrc.py as
# DIR\.pymolrc and DIR\.pymolrc.py, where DIR is -Prefix or %USERPROFILE%
# ($HOME on Linux and macOS). Native Windows counterpart of install.sh; runs on
# PowerShell 7 (pwsh) and Windows PowerShell 5.1.
#
#   pwsh -NoProfile -File install.ps1 [-Prefix DIR] [-Force]
#
# Source: the configs\ directory next to this script when the script is named
# install.ps1, sits in a git checkout (.git next to it), configs\ holds both
# files, and .git, configs\ and both files belong to the user running it (on
# Windows: owned by that user, or by the owner that user's new files get) and
# are not links; otherwise both are downloaded with Invoke-WebRequest from
#   $env:PYMOLSCRIPTS_BASE_URL (default https://raw.githubusercontent.com/FridrichMethod/PyMOLScripts)
#   /$env:PYMOLSCRIPTS_REF (default main)/configs/<name>
# A copy run from a shared directory such as C:\Temp or /tmp therefore never
# installs a .git and configs\ that someone else put next to it, whatever its
# name.
#
# The same command installs and updates. After writing a file it keeps a copy
# of the bytes it wrote, the record, at
#   %LOCALAPPDATA%\pymolscripts\installed\<drive>\<path of DIR>\<name>
# on Windows (LOCALAPPDATA, else the Local AppData known folder) and, like
# install.sh, at ${XDG_STATE_HOME:-$HOME/.local/state}/pymolscripts/installed<DIR>/<name>
# elsewhere, in a directory created private. Before writing anything it
# classifies both files, comparing with CR bytes ignored because PyMOL's
# File > Edit pymolrc saves these CRLF files with LF on Linux and macOS:
#   - absent, or a dangling symlink: installed;
#   - byte-identical to the new upstream: left alone, and only recorded;
#   - otherwise equal to the new upstream, to the record or, with no record
#     when downloading, to the configs at $LegacyRef: updated;
#   - anything else is a local edit: nothing at all is written, a message on
#     stderr says how to keep or overwrite the edit, and the exit status is 3.
# Files installed before records existed have none; they are recorded or
# updated without a stop when they match the new upstream or the configs at
# $LegacyRef, which is what the installer always wrote before.
# -Force overwrites local edits after copying each edited regular file to
# DIR\<name>.local-yyyyMMddHHmmss (plus -N when taken), a name PyMOL never
# runs; an edited symlink is replaced and its target left untouched.
# Host-only settings belong in DIR\.pymolrc.local.pml, which PyMOL runs after
# both files and this installer never touches.
#
# Changed files are written to a temporary directory inside DIR and renamed
# into place, which replaces a symlink instead of writing through it; nothing
# is renamed unless both files were fetched, and the records are written only
# after the renames, so an interrupted run never makes the next one stop.
#
# Exit status: 0 success (including "already current"), 1 error, 2 usage
# error, 3 stopped for a local edit (nothing was changed).
#
# Arguments are parsed from $args rather than a param() block so that an
# unknown or incomplete argument is a usage error (exit 2) instead of being
# ignored or reported by the parameter binder. Only the form -Prefix DIR is
# accepted: PowerShell itself splits -Name:value and -Name=C:\x tokens.

Set-StrictMode -Version 3.0
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'

$Prog = 'install.ps1'
$Names = @('.pymolrc', '.pymolrc.py')
$DefaultBaseUrl = 'https://raw.githubusercontent.com/FridrichMethod/PyMOLScripts'
# The configs every install wrote before records existed. An unrecorded file
# equal to them is updated rather than reported as a local edit. Remove after
# 2027-04-30, by when every host has run this installer and holds a record.
$LegacyRef = '41785ca151497dce4e613ef9e19f812102a7b564'
$OnWindows = ($PSVersionTable.PSEdition -eq 'Desktop') -or
    ((Get-Variable -Name IsWindows -ValueOnly -ErrorAction Ignore) -eq $true)
$HomeVariable = if ($OnWindows) { 'USERPROFILE' } else { 'HOME' }
$HomeShown = if ($OnWindows) { '%USERPROFILE%' } else { '$HOME' }
$StateShown = if ($OnWindows) { '%LOCALAPPDATA%\pymolscripts' } else { '${XDG_STATE_HOME:-$HOME/.local/state}/pymolscripts' }
$Shell = if ($PSVersionTable.PSEdition -eq 'Desktop') { 'powershell' } else { 'pwsh' }

function Write-Usage {
    @(
        'Usage: install.ps1 [-Prefix DIR] [-Force]'
        ''
        "Install PyMOLScripts' .pymolrc and .pymolrc.py into DIR (default: $HomeShown)."
        'Rerun the same command to update; files that are already current are left'
        'untouched. A copy of what was installed is kept under'
        "$StateShown. A file that differs from"
        'it and from the new upstream is a local edit: the run then writes nothing,'
        'says how to keep or overwrite the edit, and exits 3. Files installed before'
        'these copies existed count as unedited when they match the new or the'
        'previous upstream. Put host-only settings in DIR\.pymolrc.local.pml, which'
        'PyMOL runs after both files and this installer never touches.'
        ''
        'Options:'
        "  -Prefix DIR  absolute directory to install into (default: $HomeShown)"
        '  -Force       overwrite local edits, first copying each edited file to'
        '               DIR\<name>.local-yyyyMMddHHmmss'
        '  -Help        show this help and exit'
        ''
        'Environment:'
        '  PYMOLSCRIPTS_REF       branch, tag or commit to download (default: main)'
        '  PYMOLSCRIPTS_BASE_URL  download base URL'
        "                         (default: $DefaultBaseUrl)"
        ''
        'Exit status: 0 done or already current, 1 error, 2 usage error,'
        '3 stopped for a local edit (nothing was changed).'
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

# A symlink or junction, dangling or not.
function Test-Link([string]$Path) {
    $item = Get-Item -LiteralPath $Path -Force -ErrorAction Ignore
    return $null -ne $item -and @('SymbolicLink', 'Junction') -contains [string]$item.LinkType
}

# $Path is no link and belongs to the user running this script: on Windows
# its owner is this user or the owner this user's new files get (the
# Administrators group in an elevated session on Windows Server); elsewhere
# its uid is the one id -u prints. An owner it cannot read is not the user's,
# and the note says why.
$Uid = $null
function Test-Mine([string]$Path) {
    if (Test-Link $Path) { return $false }
    try {
        if ($OnWindows) {
            if ($PSVersionTable.PSEdition -eq 'Desktop') {
                # .NET Framework reads the owner itself. Get-Acl would autoload
                # Microsoft.PowerShell.Security, which fails when PSModulePath
                # lists PowerShell 7's modules first, as it does in a Windows
                # PowerShell started by a program that pwsh started.
                $sections = [Security.AccessControl.AccessControlSections]::Owner
                $acl = if ([IO.Directory]::Exists($Path)) {
                    [IO.Directory]::GetAccessControl($Path, $sections)
                } else {
                    [IO.File]::GetAccessControl($Path, $sections)
                }
            } else {
                $acl = Get-Acl -LiteralPath $Path
            }
            $owner = $acl.GetOwner([Security.Principal.SecurityIdentifier])
            $me = [Security.Principal.WindowsIdentity]::GetCurrent()
            return ($null -ne $owner) -and (($owner -eq $me.User) -or ($owner -eq $me.Owner))
        }
        if ($null -eq $script:Uid) { $script:Uid = ([string](& id -u)).Trim() }
        $owner = [string](Get-Item -LiteralPath $Path -Force).UnixStat.UserId
        return ($script:Uid -ne '') -and ($owner -eq $script:Uid)
    } catch {
        [Console]::Error.WriteLine("${Prog}: note: cannot read the owner of ${Path}: $($_.Exception.Message)")
        return $false
    }
}

# A directory, a link to one, or a directory-type link whose target is gone:
# entries a file rename cannot replace.
function Test-DirectoryEntry([string]$Path) {
    if ([IO.Directory]::Exists($Path)) { return $true }
    $item = Get-Item -LiteralPath $Path -Force -ErrorAction Ignore
    return $null -ne $item -and [bool]($item.Attributes -band [IO.FileAttributes]::Directory)
}

# The bytes of $Path, read through links; $null when it is absent or a
# dangling link.
function Read-Bytes([string]$Path) {
    try {
        return , [IO.File]::ReadAllBytes($Path)
    } catch [IO.FileNotFoundException], [IO.DirectoryNotFoundException] {
        return $null
    }
}

function Test-SameBytes([byte[]]$X, [byte[]]$Y) {
    if ($X.Length -ne $Y.Length) { return $false }
    for ($i = 0; $i -lt $X.Length; $i++) {
        if ($X[$i] -ne $Y[$i]) { return $false }
    }
    return $true
}

# The same bytes once every CR (13) is deleted from both.
function Test-SameText([byte[]]$X, [byte[]]$Y) {
    $i = 0
    $j = 0
    while ($true) {
        while ($i -lt $X.Length -and $X[$i] -eq 13) { $i++ }
        while ($j -lt $Y.Length -and $Y[$j] -eq 13) { $j++ }
        if ($i -ge $X.Length -or $j -ge $Y.Length) {
            return ($i -ge $X.Length) -and ($j -ge $Y.Length)
        }
        if ($X[$i] -ne $Y[$j]) { return $false }
        $i++
        $j++
    }
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
    } elseif ([IO.File]::Exists($To) -and -not (Test-Link $To)) {
        # [NullString]::Value: PowerShell would pass $null as "" (an invalid path).
        [IO.File]::Replace($From, $To, [NullString]::Value)
    } else {
        [IO.File]::Delete($To)
        [IO.File]::Move($From, $To)
    }
}

function Get-Message($ErrorRecord) {
    return ($ErrorRecord.Exception.Message -replace '\s+', ' ').Trim().TrimEnd('.')
}

# $Word as a PowerShell single-quoted string. PowerShell ends one at U+2018,
# U+2019, U+201A and U+201B as well as at U+0027, and the escaping function
# of PowerShell's own code generation doubles each of them.
function ConvertTo-Quoted([string]$Word) {
    return "'" + [Management.Automation.Language.CodeGeneration]::EscapeSingleQuotedStringContent($Word) + "'"
}

# Where the records of what was installed into $Dir live.
function Get-RecordDir([string]$Dir) {
    if (-not $OnWindows) {
        $base = $env:XDG_STATE_HOME
        if ([string]::IsNullOrEmpty($base) -or -not $base.StartsWith('/')) {
            $homeDir = $env:HOME
            if ([string]::IsNullOrEmpty($homeDir) -or -not $homeDir.StartsWith('/')) {
                Exit-Error 'cannot locate the record directory; set XDG_STATE_HOME or HOME to an absolute path'
            }
            $base = "$homeDir/.local/state"
        }
        return $base.TrimEnd('/') + '/pymolscripts/installed' + $Dir.TrimEnd('/')
    }
    $base = $env:LOCALAPPDATA
    if ([string]::IsNullOrEmpty($base) -or -not (Test-AbsolutePath $base)) {
        $base = [Environment]::GetFolderPath('LocalApplicationData')
    }
    if ([string]::IsNullOrEmpty($base)) {
        Exit-Error 'cannot locate the record directory; set LOCALAPPDATA'
    }
    # C:\x -> C\x, \\server\share\x -> UNC\server\share\x, \\?\C:\x -> C\x.
    $path = $Dir -replace '^[\\/]{2}[?.][\\/]', ''
    if ($path -match '^([A-Za-z]):(.*)$') {
        $path = $Matches[1] + '\' + $Matches[2]
    } elseif ($path -match '^[\\/]{2}(.*)$') {
        $path = 'UNC\' + $Matches[1]
    }
    $parts = @($path -split '[\\/]+' | Where-Object { $_ -ne '' })
    return [IO.Path]::Combine([string[]](@($base, 'pymolscripts', 'installed') + $parts))
}

# Create $Path and any missing parent, each readable only by its owner.
function New-PrivateDirectory([string]$Path) {
    $missing = @()
    $p = $Path
    while ($p -and -not [IO.Directory]::Exists($p)) {
        $missing = @($p) + $missing
        $p = [IO.Path]::GetDirectoryName($p)
    }
    $modeType = 'System.IO.UnixFileMode' -as [type]
    foreach ($d in $missing) {
        [void][IO.Directory]::CreateDirectory($d)
        if ($OnWindows) { continue } # %LOCALAPPDATA% is private already
        if ($null -ne $modeType) {
            [IO.File]::SetUnixFileMode($d, [Enum]::Parse($modeType, 'UserRead, UserWrite, UserExecute'))
        } else {
            & chmod 700 -- $d
            if ($LASTEXITCODE -ne 0) { throw "chmod 700 $d failed" }
        }
    }
}

function Save-Download([string]$Url, [string]$Out) {
    try {
        Invoke-WebRequest -Uri $Url -OutFile $Out -UseBasicParsing -TimeoutSec 300
    } catch {
        Exit-Error "download failed ($(Get-Message $_)): $Url"
    }
    if (-not [IO.File]::Exists($Out) -or (New-Object IO.FileInfo $Out).Length -eq 0) {
        Exit-Error "downloaded file is empty: $Url"
    }
}

# Copy the edited $Path to a free $Path.local-<time>; return that name.
function Backup-Edit([string]$Path) {
    $base = $Path + '.local-' + (Get-Date -Format 'yyyyMMddHHmmss')
    $backup = $base
    $n = 0
    while ([IO.File]::Exists($backup) -or [IO.Directory]::Exists($backup) -or (Test-Link $backup)) {
        $n++
        $backup = "$base-$n"
    }
    [IO.File]::Copy($Path, $backup, $false)
    [IO.File]::SetLastWriteTimeUtc($backup, [IO.File]::GetLastWriteTimeUtc($Path))
    return $backup
}

# The command that reruns this installer with -Force. A download takes the
# installer from main, as the README does, and only the configs from
# PYMOLSCRIPTS_REF: the installer at an older ref has no -Force, or is none.
function Get-OverrideCommand {
    $prefixArg = if ($null -ne $Prefix) { ' -Prefix ' + (ConvertTo-Quoted $Dir) } else { '' }
    if ($null -eq $Stage) {
        return "$Shell -NoProfile -ExecutionPolicy Bypass -File $(ConvertTo-Quoted $PSCommandPath) -Force$prefixArg"
    }
    $line = ''
    foreach ($var in @('PYMOLSCRIPTS_REF', 'PYMOLSCRIPTS_BASE_URL')) {
        $value = [Environment]::GetEnvironmentVariable($var)
        if (-not [string]::IsNullOrEmpty($value)) {
            $line += "`$env:$var = $(ConvertTo-Quoted $value); "
        }
    }
    $url = ConvertTo-Quoted "$BaseUrl/main/install.ps1"
    return $line + '$f = Join-Path ([IO.Path]::GetTempPath()) "PyMOLScripts-install-$PID.ps1"; ' +
        "Invoke-WebRequest -UseBasicParsing -ErrorAction Stop -OutFile `$f $url; " +
        "$Shell -NoProfile -ExecutionPolicy Bypass -File `$f -Force$prefixArg; Remove-Item `$f"
}

# At most 10 lines, so that a caller keeping the last 20 lines keeps it all.
function Write-Edits {
    $lines = @("${Prog}: stopped: a local edit would be overwritten; nothing was changed.")
    foreach ($name in $Names) {
        $s = $State[$name]
        if ($s.Action -ne 'edited') { continue }
        $dest = [IO.Path]::Combine($Dir, $name)
        $record = [IO.Path]::Combine($RecordDir, $name)
        if ($s.Link) {
            $lines += "  edited: $dest (a symlink; -Force replaces it, not its target)"
        } else {
            $lines += "  edited: $dest"
        }
        $q = (ConvertTo-Quoted $record), (ConvertTo-Quoted $dest)
        if ($null -ne $s.Recorded -and $OnWindows) {
            # @(): Get-Content of an empty file is $null, which Compare-Object refuses.
            $lines += "    see the edit: Compare-Object @(Get-Content -LiteralPath $($q[0])) @(Get-Content -LiteralPath $($q[1]))"
        } elseif ($null -ne $s.Recorded -and (Get-Command -Name diff -CommandType Application -ErrorAction Ignore)) {
            $lines += "    see the edit: diff -u $($q[0]) $($q[1])"
        } elseif ($null -ne $Stage) {
            $lines += "    compare with: $BaseUrl/$Ref/configs/$name"
        } else {
            $lines += "    compare with: $([IO.Path]::Combine($Src, $name))"
        }
    }
    $lines += 'To keep an edit, commit it to configs/ in PyMOLScripts (the next run then'
    $lines += "finds it current), or move host-only settings to $([IO.Path]::Combine($Dir, '.pymolrc.local.pml')),"
    $lines += 'which PyMOL runs after both files and this installer never touches.'
    $lines += 'To overwrite instead, keeping each edited file as <name>.local-<time>:'
    $lines += '  ' + (Get-OverrideCommand)
    foreach ($line in $lines) { [Console]::Error.WriteLine($line) }
}

# --- arguments ---------------------------------------------------------------

$Prefix = $null
$Force = $false
$i = 0
while ($i -lt $args.Count) {
    $arg = [string]$args[$i]
    if ($arg -match '^(-|--)prefix$') {
        if ($i + 1 -ge $args.Count) { Exit-Usage '-Prefix needs a directory' }
        $Prefix = [string]$args[$i + 1]
        $i += 2
        continue
    }
    if ($arg -match '^(-|--)force$') {
        $Force = $true
        $i++
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
$RecordDir = Get-RecordDir $Dir

$Stage = $null   # download staging directory under the system temp directory
$Work = $null    # temporary directory inside $Dir, created only when a file changes
$Partial = $null # record being written
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
            $record = [IO.Path]::Combine($RecordDir, $name)
            if (Test-DirectoryEntry $record) {
                Exit-Error "$record is not a regular file; remove it and rerun"
            }
        }

        # --- source ------------------------------------------------------------
        # Run from a file named install.ps1 in a git checkout: install its configs.
        $Src = $null
        if ($PSCommandPath -and [IO.Path]::GetFileName($PSCommandPath) -eq 'install.ps1') {
            $root = [IO.Path]::GetDirectoryName($PSCommandPath)
            $git = [IO.Path]::Combine($root, '.git')
            $checkout = [IO.Path]::Combine($root, 'configs')
            $checkoutFiles = @($Names | ForEach-Object { [IO.Path]::Combine($checkout, $_) })
            if (([IO.Directory]::Exists($git) -or [IO.File]::Exists($git)) -and
                [IO.File]::Exists($checkoutFiles[0]) -and [IO.File]::Exists($checkoutFiles[1])) {
                # In a shared directory such as C:\Temp or /tmp anyone can create
                # .git and configs beside a copy of this script, so only the
                # user's own are a checkout.
                if ((Test-Mine $git) -and (Test-Mine $checkout) -and
                    (Test-Mine $checkoutFiles[0]) -and (Test-Mine $checkoutFiles[1])) {
                    $Src = $checkout
                } else {
                    [Console]::Error.WriteLine("${Prog}: note: ignoring ${checkout}: it, its files and .git must be yours and no symlinks; downloading instead")
                }
            }
        }
        if ($null -eq $Src) {
            if ($PSVersionTable.PSEdition -eq 'Desktop') {
                # raw.githubusercontent.com needs TLS 1.2, which .NET Framework
                # may not offer by default.
                [Net.ServicePointManager]::SecurityProtocol =
                    [Net.ServicePointManager]::SecurityProtocol -bor [Net.SecurityProtocolType]::Tls12
            }
            $BaseUrl = $env:PYMOLSCRIPTS_BASE_URL
            if ([string]::IsNullOrEmpty($BaseUrl)) { $BaseUrl = $DefaultBaseUrl }
            $BaseUrl = $BaseUrl.TrimEnd('/')
            $Ref = $env:PYMOLSCRIPTS_REF
            if ([string]::IsNullOrEmpty($Ref)) { $Ref = 'main' }
            $tmpRoot = [IO.Path]::GetTempPath()
            try {
                $Stage = [IO.Path]::Combine($tmpRoot, 'pymolscripts-install.' + [IO.Path]::GetRandomFileName())
                [void][IO.Directory]::CreateDirectory($Stage)
            } catch {
                Exit-Error "cannot create a temporary directory in ${tmpRoot}: $(Get-Message $_)"
            }
            foreach ($name in $Names) {
                Save-Download "$BaseUrl/$Ref/configs/$name" ([IO.Path]::Combine($Stage, $name))
            }
            $Src = $Stage
        }

        # --- classify ------------------------------------------------------------
        # The record is read before the file: every run renames a file before
        # it writes its record, so a concurrent run can never pair an old file
        # with a newer record and make it look edited.
        $State = @{}
        $edited = $false
        $writes = $false # files to write
        $needed = $false # files or records to write
        foreach ($name in $Names) {
            $dest = [IO.Path]::Combine($Dir, $name)
            $record = [IO.Path]::Combine($RecordDir, $name)
            $upstream = [IO.File]::ReadAllBytes([IO.Path]::Combine($Src, $name))
            $recorded = $null
            if ([IO.File]::Exists($record)) { $recorded = [IO.File]::ReadAllBytes($record) }
            $stale = ($null -eq $recorded) -or -not (Test-SameBytes $recorded $upstream)
            $isLink = Test-Link $dest
            $found = Read-Bytes $dest
            if ($null -eq $found) {
                $action = 'install'
            } elseif (Test-SameBytes $found $upstream) {
                $action = 'current'
            } elseif (Test-SameText $found $upstream) {
                $action = 'update'
            } elseif ($null -ne $recorded) {
                $action = if (Test-SameText $found $recorded) { 'update' } else { 'edited' }
            } elseif ($null -ne $Stage) {
                $legacyDir = [IO.Path]::Combine($Stage, 'legacy')
                [void][IO.Directory]::CreateDirectory($legacyDir)
                $legacyFile = [IO.Path]::Combine($legacyDir, $name)
                Save-Download "$BaseUrl/$LegacyRef/configs/$name" $legacyFile
                $legacy = [IO.File]::ReadAllBytes($legacyFile)
                $action = if (Test-SameText $found $legacy) { 'update' } else { 'edited' }
            } else {
                $action = 'edited'
            }
            $State[$name] = @{
                Action = $action; Found = $found; Link = $isLink; Stale = $stale
                Upstream = $upstream; Recorded = $recorded
            }
            if ($action -eq 'edited') { $edited = $true }
            if ($action -ne 'current') { $writes = $true; $needed = $true }
            if ($stale) { $needed = $true }
        }

        # --- stop for a local edit ---------------------------------------------
        if ($edited -and -not $Force) {
            Write-Edits
            exit 3
        }
        if (-not $needed) {
            Write-Status ".pymolrc and .pymolrc.py in $Dir are already current"
            exit 0
        }

        # --- write ---------------------------------------------------------------
        # The record directory comes first, so that a broken state directory
        # stops the run before anything in DIR changes.
        try {
            New-PrivateDirectory $RecordDir
        } catch {
            Exit-Error "cannot create ${RecordDir}: $(Get-Message $_)"
        }

        $wrote = $false
        if ($writes) {
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
            foreach ($name in $Names) {
                if ($State[$name].Action -eq 'current') { continue }
                $tmp = [IO.Path]::Combine($Work, $name)
                try {
                    [IO.File]::WriteAllBytes($tmp, $State[$name].Upstream)
                } catch {
                    Exit-Error "cannot write ${tmp}: $(Get-Message $_)"
                }
            }
            foreach ($name in $Names) {
                $s = $State[$name]
                if ($s.Action -eq 'current') { continue }
                $dest = [IO.Path]::Combine($Dir, $name)
                $note = ''
                if ($s.Action -eq 'install') {
                    $verb = 'installed'
                } elseif ($s.Link) {
                    $verb = 'updated'
                    $note = ' (replaced a symlink; its target is untouched)'
                } elseif ($s.Action -eq 'edited') {
                    try {
                        $backup = Backup-Edit $dest
                    } catch {
                        Exit-Error "cannot copy $dest to a backup: $(Get-Message $_)"
                    }
                    $verb = 'updated'
                    $note = " (kept the edited file as $backup)"
                } else {
                    $verb = 'updated'
                }
                # Narrow the window for an edit made since the file was read.
                $now = Read-Bytes $dest
                $unchanged = if ($s.Action -eq 'install') { $null -eq $now } else {
                    ($null -ne $now) -and (Test-SameBytes $now $s.Found)
                }
                if (-not $unchanged) {
                    if (($null -ne $now) -and (Test-SameBytes $now $s.Upstream)) {
                        continue # another run installed it meanwhile
                    }
                    Exit-Error "$dest changed while installing; nothing more was changed, rerun to check it again"
                }
                if (Test-DirectoryEntry $dest) {
                    Exit-Error "$dest is a directory or a link to one; remove it and rerun"
                }
                try {
                    Move-Replace ([IO.Path]::Combine($Work, $name)) $dest
                } catch {
                    Exit-Error "cannot rename into ${dest}: $(Get-Message $_)"
                }
                Write-Status "$verb $dest$note"
                $wrote = $true
            }
        }

        # Records last: a run that stops before here leaves files the next run
        # finds current, never a record newer than the file it describes.
        foreach ($name in $Names) {
            if (-not $State[$name].Stale) { continue }
            $record = [IO.Path]::Combine($RecordDir, $name)
            try {
                $Partial = [IO.Path]::Combine($RecordDir, '.record.' + [IO.Path]::GetRandomFileName())
                [IO.File]::WriteAllBytes($Partial, $State[$name].Upstream)
                Move-Replace $Partial $record
                $Partial = $null
            } catch {
                Exit-Error "cannot write the record ${record}: $(Get-Message $_); the next run writes it"
            }
        }
        if (-not $wrote) {
            Write-Status ".pymolrc and .pymolrc.py in $Dir are already current"
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
    if ($Partial -and [IO.File]::Exists($Partial)) {
        try { [IO.File]::Delete($Partial) } catch { }
    }
}
exit 0
