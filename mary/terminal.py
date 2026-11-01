"""The terminal as MARY's general-purpose tool: PowerShell decides whether a command only looks, runs it, and hands
back its output trimmed and structured.

Only looks: PowerShell's own parser reads the command line (Parser.ParseInput) and reports every command in it,
pipelines, sub-expressions and script blocks included, with aliases resolved, plus every method call, assignment
and redirection. The policy here then says yes only when all of it is known to read, list or measure:
- every command is one of LOOKING (after alias resolution), called by name, with no splatting, and with none of
  the parameters PARAMS forbids (Get-Help -Online opens a browser; ForEach-Object Kill calls a method)
- every method call is a pure one (string, date and maths work, Get…) or a static one on a type in STATIC
- nothing writes to a file (> file), runs code (& or ., Invoke-Expression), sets a property or a drive item
  (${C:\\x.txt} = …), or defines functions, classes or modules
Anything else, including anything PowerShell can't parse and any program or script, counts as a change, and
run_powershell shows it and asks first. A wrong "changes" costs one question; a wrong "only looks" could cost
data, so every doubt goes that way. The blocklist (config safety.powershell_blocklist) still refuses outright.

Output: objects come back as a table in PowerShell's own view of them (its views pick the columns), one object as
PowerShell shows it, plain values as lines; at most HEAD + TAIL objects are formatted, and long output keeps its beginning and its end with
"… n lines skipped …" between. Errors are grouped ("Access is denied" ×300 is one line).
"""
from __future__ import annotations

import base64
import json
import logging
import re
import subprocess
from dataclasses import dataclass
from functools import lru_cache

log = logging.getLogger(__name__)

WIDTH = 120            # characters per line of a table
HEAD, TAIL = 60, 10    # objects formatted from the start and the end of a long result
CLASSIFY_TIMEOUT_S = 15
MARKER = "@@MARY-RESULT@@"

# Commands that only read, list, measure or shape output. Names, not verbs: some Get- commands do write
# (Get-WindowsUpdateLog writes a file), so each one here is known to only look.
LOOKING = frozenset(n.lower() for n in """
    Get-ChildItem Get-Item Get-Content Get-ItemProperty Get-ItemPropertyValue Get-Location Get-PSDrive
    Get-PSProvider Get-Acl Get-FileHash Get-AuthenticodeSignature Test-Path Resolve-Path Split-Path Join-Path
    Convert-Path Select-String Select-Xml Import-Csv Format-Hex
    Get-Process Get-Service Get-HotFix Get-ComputerInfo Get-EventLog Get-WinEvent Get-Counter Get-WmiObject
    Get-CimInstance Get-CimClass Get-CimAssociatedInstance Get-Date Get-TimeZone Get-Culture Get-UICulture
    Get-Host Get-Uptime Get-Clipboard Get-ExecutionPolicy Get-ComputerRestorePoint Get-ScheduledTask
    Get-ScheduledTaskInfo Get-AppxPackage Get-StartApps Get-Package Get-PnpDevice Get-PnpDeviceProperty
    Get-Printer Get-PrintJob Get-LocalUser Get-LocalGroup Get-LocalGroupMember Get-MpComputerStatus
    Get-MpThreat Get-MpThreatDetection Get-Volume Get-Disk Get-Partition Get-PhysicalDisk Get-WinSystemLocale
    Get-WinUserLanguageList Get-WinHomeLocation
    Get-NetIPAddress Get-NetIPConfiguration Get-NetIPInterface Get-NetAdapter Get-NetAdapterStatistics
    Get-NetConnectionProfile Get-NetRoute Get-NetTCPConnection Get-NetUDPEndpoint Get-NetNeighbor
    Get-NetFirewallRule Get-NetFirewallProfile Get-DnsClient Get-DnsClientCache Get-DnsClientServerAddress
    Get-SmbShare Get-SmbMapping Get-VpnConnection Test-Connection Test-NetConnection Resolve-DnsName
    Get-Command Get-Help Get-Module Get-Alias Get-Variable Get-Member Get-History Get-Job Get-Verb Get-TypeData
    Get-FormatData Get-Random Get-Unique New-TimeSpan New-Guid
    Where-Object ForEach-Object Select-Object Sort-Object Group-Object Measure-Object Compare-Object
    Measure-Command Format-Table Format-List Format-Wide Format-Custom Out-String Out-Null Out-Host Out-Default
    ConvertTo-Json ConvertFrom-Json ConvertTo-Csv ConvertFrom-Csv ConvertTo-Html ConvertTo-Xml
    ConvertFrom-StringData Write-Output Write-Host Write-Verbose Write-Warning Write-Error Write-Debug
    Write-Information Set-Location Push-Location Pop-Location
""".split())

_COMMON = {"verbose", "debug", "erroraction", "warningaction", "informationaction", "errorvariable",
           "warningvariable", "informationvariable", "outvariable", "outbuffer", "pipelinevariable"}
_BLOCKS = {"process", "begin", "end", "remainingscripts"}


def _only_blocks(c: dict) -> str | None:
    """ForEach-Object runs script blocks (read like the rest of the line); given a name instead
    (ForEach-Object Kill, -MemberName), it calls that method on everything."""
    bound = {p.lower() for p in c["bound"]}
    other = bound - _BLOCKS - _COMMON
    if other:
        name = next(p for p in sorted(c["bound"]) if p.lower() in other)
        return f"ForEach-Object with -{name} can call methods"
    if _BLOCKS & {p.lower() for p in c["nonblock"]}:
        return "ForEach-Object is given something other than a script block"
    return None


def _forbid(*names: str):
    def rule(c: dict) -> str | None:
        hit = sorted(p for p in c["bound"] if p.lower() in names)
        return f"{c['resolved']} -{hit[0]} does more than look" if hit else None
    return rule


# Per-command parameter rules, checked on PowerShell's own binding of the line (abbreviations resolved).
PARAMS = {
    "get-help": _forbid("online", "showwindow"),
    "foreach-object": _only_blocks,
}

# Static methods: on these types every method is pure (maths, dates, text)...
_PURE_TYPES = {"math", "datetime", "datetimeoffset", "timespan", "string", "char", "convert", "bitconverter",
               "guid", "version", "uri", "regex", "text.regularexpressions.regex", "int", "int16", "int32",
               "int64", "long", "short", "uint16", "uint32", "uint64", "byte", "sbyte", "double", "float",
               "single", "decimal", "bool", "boolean", "bigint", "numerics.biginteger", "dayofweek"}
# ... and on these only the ones that read.
STATIC = {
    "io.path": lambda m: m != "gettempfilename",   # it creates the file
    "io.file": lambda m: m.startswith(("read", "get")) or m == "exists",
    "io.directory": lambda m: m.startswith(("get", "enumerate")) or m == "exists",
    "io.driveinfo": lambda m: m == "getdrives",
    "environment": lambda m: m.startswith(("get", "expand")),
}
# Instance methods that only compute: text, dates, collections. Get… reads (GetFiles, GetValue, GetOwner), except
# the web requests. ForEach/Where are fine with script blocks (read like the rest); with a name they call it.
_PURE_METHODS = set("""
    tostring toupper tolower toupperinvariant tolowerinvariant trim trimstart trimend split substring contains
    startswith endswith indexof indexofany lastindexof lastindexofany padleft padright equals compareto normalize
    tochararray toarray containskey containsvalue toshortdatestring tolongdatestring toshorttimestring
    tolongtimestring tolocaltime touniversaltime tofiletime adddays addhours addminutes addseconds
    addmilliseconds addmonths addyears addticks subtract isdaylightsavingtime
""".split())
_GET_THAT_SEND = {"getresponse", "getrequeststream", "getresponseasync", "getrequeststreamasync"}

_OTHER = {"function": "it defines a function", "class": "it defines a class", "using": "it loads a module",
          "requires": "it loads modules (#requires)", "keyword": "it runs a configuration keyword",
          "configuration": "it defines a configuration"}
_PROGRAM = re.compile(r"[\\/:]|\.(exe|com|bat|cmd|ps1|psm1|vbs|vbe|js|jse|wsf|msc|cpl|lnk|py)$", re.I)


@dataclass(frozen=True)
class Verdict:
    looks: bool
    why: str = ""   # what makes it a change ("" when it only looks)


def judge(facts: dict) -> Verdict:
    """The policy, applied to what PowerShell's parser found in one command line (see _FACTS)."""
    if facts.get("errors"):
        return Verdict(False, f"PowerShell can't parse it: {facts['errors'][0]}")
    for kind in facts.get("other", []):
        return Verdict(False, _OTHER.get(kind, f"it uses {kind}"))
    for c in facts.get("commands", []):
        why = _command(c)
        if why:
            return Verdict(False, why)
    for m in facts.get("members", []):
        why = _member(m)
        if why:
            return Verdict(False, why)
    if any(r != "null" for r in facts.get("redirects", [])):
        return Verdict(False, "it writes to a file (>)")
    for target in facts.get("assigns", []):
        if target == "drive":
            return Verdict(False, "it sets a drive item (an environment variable or a file)")
        if target != "variable":
            return Verdict(False, "it sets a property")
    return Verdict(True)


def _command(c: dict) -> str | None:
    name, resolved = c.get("name"), c.get("resolved") or ""
    if c.get("invocation") not in (None, "", "Unknown"):
        return "it runs code with & or ."
    if not name:
        return "what it runs is only known when it runs"
    if c.get("splat"):
        return "its parameters are hidden in a splat (@)"
    low = resolved.lower()
    if _PROGRAM.search(low):
        return f"it runs a program or script ({name})"
    if low not in LOOKING:
        aka = f" ({resolved})" if resolved.lower() != name.lower() else ""
        return f"{name}{aka} isn't one of the commands that only look"
    rule = PARAMS.get(low)
    if rule:
        if c.get("bound") is None:
            return f"PowerShell can't bind {resolved}'s parameters"
        return rule(c)
    return None


def _member(m: dict) -> str | None:
    name = (m.get("name") or "").lower()
    if not name:
        return "it calls a method chosen when it runs"
    if m.get("static"):
        kind = (m.get("type") or "").lower().removeprefix("system.")
        rule = STATIC.get(kind)
        if kind in _PURE_TYPES or (rule and rule(name)):
            return None
        return f"it calls [{m.get('type') or '?'}]::{m.get('name')}()"
    if name in ("foreach", "where"):
        return None if m.get("blocks") else f"it calls .{m.get('name')}() with a name, which can call methods"
    if name in _PURE_METHODS or (name.startswith("get") and name not in _GET_THAT_SEND):
        return None
    return f"it calls the method {m.get('name')}()"


# ------------------------------------------------------------------ PowerShell's side

# Reads {"lines": [...], "bind": [...]} (base64 JSON on stdin) and writes, per line, what its parser found.
# Parameters are bound (StaticParameterBinder) only for the commands that have rules, as binding loads modules.
_FACTS = r"""
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
$req = [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String([Console]::In.ReadToEnd().Trim())) | ConvertFrom-Json
$bind = @{}; foreach ($n in @($req.bind)) { $bind[$n] = $true }
$aliases = @{}; foreach ($a in Get-Alias) { $aliases[$a.Name] = $a.Definition }
function Target($ast) {
    while ($ast -is [System.Management.Automation.Language.AttributedExpressionAst]) { $ast = $ast.Child }
    if ($ast -is [System.Management.Automation.Language.VariableExpressionAst]) {
        if ($ast.VariablePath.IsDriveQualified) { return 'drive' } else { return 'variable' }
    }
    if ($ast -is [System.Management.Automation.Language.ArrayLiteralAst]) {
        $kinds = @($ast.Elements | ForEach-Object { Target $_ })
        if ($kinds -contains 'other') { return 'other' }
        if ($kinds -contains 'drive') { return 'drive' }
        return 'variable'
    }
    return 'other'
}
$out = New-Object System.Collections.ArrayList
foreach ($line in @($req.lines)) {
    $tokens = $null; $errors = $null
    $ast = [System.Management.Automation.Language.Parser]::ParseInput([string]$line, [ref]$tokens, [ref]$errors)
    $f = @{ errors = @($errors | ForEach-Object { $_.Message }); commands = New-Object System.Collections.ArrayList
            members = New-Object System.Collections.ArrayList; assigns = New-Object System.Collections.ArrayList
            redirects = New-Object System.Collections.ArrayList; other = New-Object System.Collections.ArrayList }
    if ($ast.ScriptRequirements) { [void]$f.other.Add('requires') }
    foreach ($n in $ast.FindAll({ $true }, $true)) {
        switch ($n.GetType().Name) {
            'CommandAst' {
                $name = $n.GetCommandName(); $resolved = $name; $hops = 0
                while ($resolved -and $aliases.ContainsKey($resolved) -and $hops -lt 8) { $resolved = $aliases[$resolved]; $hops++ }
                $bound = @(); $nonblock = @()
                if ($resolved -and $bind.ContainsKey($resolved)) {
                    try {
                        $b = [System.Management.Automation.Language.StaticParameterBinder]::BindCommand($n, $true)
                        foreach ($k in @($b.BoundParameters.Keys)) {
                            $bound += $k
                            if (-not ($b.BoundParameters[$k].Value -is [System.Management.Automation.Language.ScriptBlockExpressionAst])) { $nonblock += $k }
                        }
                    } catch { $bound = $null }
                }
                $splat = [bool](@($n.CommandElements | Where-Object { $_ -is [System.Management.Automation.Language.VariableExpressionAst] -and $_.Splatted }).Count)
                [void]$f.commands.Add(@{ name = $name; resolved = $resolved; invocation = "$($n.InvocationOperator)"
                                         bound = $bound; nonblock = $nonblock; splat = $splat })
            }
            'InvokeMemberExpressionAst' {
                $m = $null; if ($n.Member -is [System.Management.Automation.Language.StringConstantExpressionAst]) { $m = $n.Member.Value }
                $type = $null
                if ($n.Static -and $n.Expression -is [System.Management.Automation.Language.TypeExpressionAst]) { $type = $n.Expression.TypeName.FullName }
                $given = @($n.Arguments)
                $blocks = $given.Count -gt 0 -and -not @($given | Where-Object { -not ($_ -is [System.Management.Automation.Language.ScriptBlockExpressionAst]) }).Count
                [void]$f.members.Add(@{ static = [bool]$n.Static; type = $type; name = $m; blocks = $blocks })
            }
            'AssignmentStatementAst' { [void]$f.assigns.Add((Target $n.Left)) }
            'UnaryExpressionAst' {
                if ("$($n.TokenKind)" -in 'PlusPlus', 'MinusMinus', 'PostfixPlusPlus', 'PostfixMinusMinus') { [void]$f.assigns.Add((Target $n.Child)) }
            }
            'FileRedirectionAst' {
                $to = 'file'
                if ($n.Location -is [System.Management.Automation.Language.VariableExpressionAst] -and $n.Location.VariablePath.UserPath -eq 'null') { $to = 'null' }
                [void]$f.redirects.Add($to)
            }
            'FunctionDefinitionAst' { [void]$f.other.Add('function') }
            'TypeDefinitionAst' { [void]$f.other.Add('class') }
            'UsingStatementAst' { [void]$f.other.Add('using') }
            'DynamicKeywordStatementAst' { [void]$f.other.Add('keyword') }
            'ConfigurationDefinitionAst' { [void]$f.other.Add('configuration') }
        }
    }
    [void]$out.Add($f)
}
$json = ConvertTo-Json -InputObject @($out) -Depth 6 -Compress
[Console]::Out.Write([Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($json)))
"""

# Runs the command (base64 on stdin) exactly as written, every output stream captured, and writes the formatted
# result after MARKER. Objects: a table in PowerShell's own view of them (one object: as PowerShell shows it);
# plain values: lines.
_RUN = r"""
$ProgressPreference = 'SilentlyContinue'
[Console]::OutputEncoding = [Text.Encoding]::UTF8
$__src = [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String([Console]::In.ReadToEnd().Trim()))
$__items = New-Object System.Collections.ArrayList
$__errors = New-Object System.Collections.ArrayList
$__warnings = New-Object System.Collections.ArrayList
$__failed = $null
$global:LASTEXITCODE = 0
function __Kind($o) {
    if ($o.GetType().FullName -like 'Microsoft.PowerShell.Commands.Internal.Format.*') { return 'format' }
    if ($o -is [string] -or $o -is [ValueType]) { return 'value' }
    return 'object'
}
function __Show($list) {
    $parts = New-Object System.Collections.ArrayList
    $run = New-Object System.Collections.ArrayList; $kind = $null
    foreach ($o in @($list) + @($null)) {
        $k = if ($null -eq $o) { 'end' } else { __Kind $o }
        if ($run.Count -and $k -ne $kind) {
            $text = switch ($kind) {
                'format' { $run | Out-String -Width __WIDTH__ }
                'value' { ($run | ForEach-Object { if ($_ -is [string]) { $_ } else { ($_ | Out-String -Width __WIDTH__).Trim() } }) -join "`n" }
                default { if ($run.Count -eq 1) { $run | Out-String -Width __WIDTH__ } else { $run | Format-Table -AutoSize | Out-String -Width __WIDTH__ } }
            }
            [void]$parts.Add($text); $run.Clear()
        }
        if ($null -ne $o) { [void]$run.Add($o); $kind = $k }
    }
    return ($parts -join "`n")
}
try {
    $__sb = [ScriptBlock]::Create($__src)
    & $__sb *>&1 | ForEach-Object {
        if ($_ -is [System.Management.Automation.ErrorRecord]) { if ($__errors.Count -lt 200) { [void]$__errors.Add("$($_.Exception.Message)") } }
        elseif ($_ -is [System.Management.Automation.WarningRecord]) { [void]$__warnings.Add($_.Message) }
        elseif ($_ -is [System.Management.Automation.InformationRecord]) { [void]$__items.Add("$($_.MessageData)") }
        elseif ($_ -is [System.Management.Automation.VerboseRecord] -or $_ -is [System.Management.Automation.DebugRecord]) { }
        elseif ($null -ne $_) { [void]$__items.Add($_) }
    }
} catch {
    $__failed = $_.Exception.Message
}
$__count = $__items.Count
try {
    if ($__count -gt (__HEAD__ + __TAIL__)) {
        $__text = (__Show $__items.GetRange(0, __HEAD__)) + "`n… $($__count - __HEAD__ - __TAIL__) more items …`n" + (__Show $__items.GetRange($__count - __TAIL__, __TAIL__))
    } else {
        $__text = __Show $__items
    }
} catch {
    $__text = ($__items | Out-String -Width __WIDTH__)
}
$__json = ConvertTo-Json -Compress -Depth 3 -InputObject @{ text = "$__text"; count = $__count; errors = @($__errors);
    warnings = @($__warnings); failed = $__failed; exit = $global:LASTEXITCODE }
[Console]::Out.Write("`n__MARKER__" + [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($__json)))
"""


def _powershell(script: str, stdin: str, timeout: float) -> subprocess.CompletedProcess:
    """powershell.exe without profile or prompts, `script` passed encoded (no quoting can change it) and `stdin`
    as base64 (no console code page can garble it)."""
    encoded = base64.b64encode(script.encode("utf-16-le")).decode()
    return subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive", "-EncodedCommand", encoded],
                          input=base64.b64encode(stdin.encode("utf-8")), capture_output=True, timeout=timeout,
                          creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))


def classify_many(lines: list[str]) -> list[Verdict]:
    """Whether each command line only looks, from one PowerShell process. Any failure counts as a change."""
    if not lines:
        return []
    try:
        proc = _powershell(_FACTS, json.dumps({"lines": list(lines), "bind": sorted(PARAMS)}), CLASSIFY_TIMEOUT_S)
        facts = json.loads(base64.b64decode(proc.stdout.strip() or b"W10="))
        if isinstance(facts, dict):  # PowerShell 5.1 unwraps a one-item array
            facts = [facts]
        if len(facts) != len(lines):
            raise ValueError(f"{len(facts)} answers for {len(lines)} lines: {proc.stderr[:300]!r}")
    except (OSError, subprocess.SubprocessError, ValueError) as e:
        log.warning("couldn't check PowerShell commands: %s", e)
        return [Verdict(False, "PowerShell couldn't check it")] * len(lines)
    return [judge(f) for f in facts]


@lru_cache(maxsize=256)
def classify(command: str) -> Verdict:
    """Whether `command` only looks (cached: the same line always gets the same answer)."""
    if not command.strip():
        return Verdict(False, "it's empty")
    verdict = classify_many([command])[0]
    log.info("powershell %s: %s%s", "only looks" if verdict.looks else "changes", command[:200],
             f" ({verdict.why})" if verdict.why else "")
    return verdict


def looks_only(command: str) -> bool:
    return classify(command).looks


# ------------------------------------------------------------------ running

@dataclass
class Ran:
    ok: bool
    text: str


def run(command: str, timeout: float, limit: int = 1500) -> Ran:
    """Run `command` exactly as written and return its output, trimmed and structured, within `limit` characters."""
    script = (_RUN.replace("__WIDTH__", str(WIDTH)).replace("__HEAD__", str(HEAD)).replace("__TAIL__", str(TAIL))
              .replace("__MARKER__", MARKER))
    try:
        proc = _powershell(script, command, timeout)
    except subprocess.TimeoutExpired:
        return Ran(False, f"Timed out after {timeout:g} s.")
    except OSError as e:
        return Ran(False, f"PowerShell couldn't start: {e}")
    stdout = proc.stdout.decode("utf-8", "replace")
    extra, found, payload = stdout.rpartition(MARKER)
    if not found:  # the command ended PowerShell itself (exit), or PowerShell failed before the end
        res = {"text": stdout, "errors": _stderr_lines(proc.stderr), "exit": proc.returncode}
        extra = ""
    else:
        try:
            res = json.loads(base64.b64decode(payload.strip()))
        except ValueError:
            return Ran(False, "PowerShell's answer couldn't be read.")
    return compose(res, extra, limit)


def _stderr_lines(raw: bytes) -> list[str]:
    text = raw.decode("utf-8", "replace")
    if text.startswith("#< CLIXML"):
        text = " ".join(re.findall(r'<S S="Error">(.*?)</S>', text)).replace("_x000D__x000A_", "\n")
    return [line.strip() for line in text.splitlines() if line.strip()][:20]


def compose(res: dict, extra: str = "", limit: int = 1500) -> Ran:
    """The tool's result from what the run reported: the output first, then what went wrong, all within `limit`."""
    errors, warnings = list(res.get("errors") or []), list(res.get("warnings") or [])
    failed, code = res.get("failed"), res.get("exit")
    notes = []
    if failed:
        notes.append(f"Failed: {_first_line(failed)}")
    if errors:
        notes.append(group_errors(errors))
    if warnings:
        notes.append("Warnings: " + "; ".join(dict.fromkeys(_first_line(w) for w in warnings[:5])))
    if code not in (None, 0):
        notes.append(f"Exit code {code}.")
    tail = "\n".join(notes)
    body = tidy("\n".join(part for part in (extra, str(res.get("text") or "")) if part.strip()))
    room = max(300, limit - len(tail) - 1)
    body = trim(body, room) if body else ("" if notes else "(no output)")
    ok = not failed and code in (None, 0) and (bool(body) or not errors)
    return Ran(ok, "\n".join(part for part in (body, tail) if part))


def _first_line(text: str) -> str:
    return next((line.strip() for line in str(text).splitlines() if line.strip()), "")


def group_errors(errors: list[str], shown: int = 3) -> str:
    """'Errors (312): Access to the path 'C:\\…' is denied. (and 311 like it)': the same error about many
    things is one line, with its first instance in full."""
    groups: dict[str, list[str]] = {}
    for e in errors:
        line = _first_line(e)
        groups.setdefault(re.sub(r"'[^']*'|\"[^\"]*\"|\d+", "_", line), []).append(line)
    lines = []
    for same in list(groups.values())[:shown]:
        more = f" (and {len(same) - 1} like it)" if len(same) > 1 else ""
        lines.append(f"- {same[0]}{more}")
    if len(groups) > shown:
        lines.append(f"- … {len(groups) - shown} other kinds of error")
    return f"Errors ({len(errors)}):\n" + "\n".join(lines)


def tidy(text: str) -> str:
    """Trailing spaces off, runs of blank lines down to one, no blank lines at either end."""
    lines = [line.rstrip() for line in text.replace("\r\n", "\n").split("\n")]
    out: list[str] = []
    for line in lines:
        if line or (out and out[-1]):
            out.append(line)
    return "\n".join(out).strip("\n")


def trim(text: str, room: int, line_chars: int = 300) -> str:
    """At most `room` characters: the beginning and the end, with '… n lines skipped …' between (never a
    silent cut). Very long lines are cut on their own."""
    lines = [line if len(line) <= line_chars else line[:line_chars - 1] + "…" for line in text.split("\n")]
    if sum(len(line) + 1 for line in lines) - 1 <= room:
        return "\n".join(lines)
    head_room, tail_room = room * 0.7, room * 0.25
    head: list[str] = []
    used = 0
    for line in lines:
        if used + len(line) + 1 > head_room:
            break
        head.append(line)
        used += len(line) + 1
    tail: list[str] = []
    used = 0
    for line in reversed(lines[len(head):]):
        if used + len(line) + 1 > tail_room:
            break
        tail.insert(0, line)
        used += len(line) + 1
    skipped = len(lines) - len(head) - len(tail)
    if not head and not tail:  # one huge line
        return lines[0][:room - 1] + "…"
    return "\n".join(head + [f"… {skipped} lines skipped …"] + tail)
