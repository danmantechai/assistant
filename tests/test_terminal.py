"""The terminal (mary/terminal.py, run_powershell): which commands only look, how output comes back, and that only
looking runs without a question while anything else still asks.

The classifier is checked against ~900 command lines in one PowerShell process: hand-picked ones (pipelines, aliases,
script blocks, method calls, redirections, sneaky ones) and every combination of common sources with filters that
only look, and with sinks that change something. The runs use plain local commands in a temp folder: no network,
nothing of yours is opened."""
from __future__ import annotations

import shutil

import pytest

from mary import terminal
from mary.config import load_config
from mary.output import Output
from mary.tools.registry import ToolContext, ToolRegistry

pytestmark = pytest.mark.skipif(shutil.which("powershell.exe") is None, reason="needs Windows PowerShell")

LOOKS = r"""
Get-ChildItem
Get-ChildItem ~/Downloads -Recurse -File | Measure-Object -Property Length -Sum
(Get-ChildItem $env:USERPROFILE\Downloads -Recurse | Measure-Object Length -Sum).Sum / 1GB
gci -Force | sort LastWriteTime -Descending | select -First 5
dir C:\
ls ~\Pictures *.jpg
Get-Process | Sort-Object CPU -Descending | Select-Object -First 5 Name, CPU
ps | where { $_.WorkingSet64 -gt 500MB } | ft Name, Id -AutoSize
Get-Service | Where-Object Status -eq Running | Measure-Object
Get-CimInstance Win32_Battery | Select-Object EstimatedChargeRemaining
(Get-CimInstance Win32_OperatingSystem).LastBootUpTime
Get-ItemProperty HKLM:\Software\Microsoft\Windows\CurrentVersion\Uninstall\* | Select-Object DisplayName, DisplayVersion
Get-ItemProperty 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Run'
Get-StartApps | Where-Object Name -like '*edge*'
Get-NetIPAddress -AddressFamily IPv4 | Select-Object IPAddress, InterfaceAlias
Get-NetAdapter | ? Status -eq Up
Test-Connection 8.8.8.8 -Count 2
Test-Path "$env:USERPROFILE\Documents\notes.txt"
Get-Content C:\Windows\System32\drivers\etc\hosts -TotalCount 20
cat .\README.md | Select-String TODO
Get-Date
(Get-Date).AddDays(30).DayOfWeek
(New-TimeSpan -Start (Get-Date) -End '2026-12-25').Days
[math]::Round((Get-PSDrive C).Free / 1GB, 1)
Get-PSDrive -PSProvider FileSystem
Get-Volume | Select-Object DriveLetter, SizeRemaining, Size
Get-PhysicalDisk | Format-Table FriendlyName, MediaType, Size
Get-ComputerInfo -Property OsName, OsVersion
Get-HotFix | Sort-Object InstalledOn -Descending | Select-Object -First 3
Get-WinEvent -LogName System -MaxEvents 10
Get-Clipboard
Get-Command *net*
Get-Help Get-ChildItem
Get-Help Get-ChildItem -Examples
Get-Member -InputObject (Get-Date)
Get-Date | Get-Member -MemberType Property
$PSVersionTable.PSVersion
$env:COMPUTERNAME
[Environment]::OSVersion.Version
[Environment]::GetFolderPath('MyDocuments')
[IO.Path]::GetExtension('report.final.pdf')
[System.IO.File]::ReadAllLines("$env:WINDIR\win.ini").Count
[IO.Directory]::GetFiles($env:TEMP).Count
[datetime]::DaysInMonth(2024, 2)
[datetime]::IsLeapYear(2100)
[TimeSpan]::FromMinutes(90).ToString()
"$($env:USERNAME) on $env:COMPUTERNAME"
'hello world'.ToUpper().Split(' ')
$files = Get-ChildItem ~\Desktop; $files.Count
$a = 1; $a++; $a
$total = 0; Get-ChildItem | ForEach-Object { $total += $_.Length }; $total
[int]$n = 5; $n * 2
$first, $rest = 1, 2, 3
1..10 | ForEach-Object { $_ * $_ }
1..10 | % { $_ * 2 } | Measure-Object -Sum
Get-Process | Group-Object Company | Sort-Object Count -Descending | Select-Object -First 5
Get-ChildItem *.log | Select-Object Name, @{n='KB'; e={ [math]::Round($_.Length / 1KB) }}
Get-ChildItem | Where-Object { $_.LastWriteTime -gt (Get-Date).AddDays(-7) }
Get-ChildItem -Recurse -ErrorAction SilentlyContinue 2>$null | Measure-Object
Get-ChildItem 2>&1 | Out-String
Get-Process *> $null
Compare-Object (Get-Content a.txt) (Get-Content b.txt)
Import-Csv .\data.csv | Measure-Object -Property Amount -Sum -Average
Get-Content .\config.json -Raw | ConvertFrom-Json | Select-Object -ExpandProperty name
Get-Process | ConvertTo-Json -Depth 1 | Out-Null
Get-FileHash .\setup.exe -Algorithm SHA256
Resolve-DnsName example.com
Get-ScheduledTask | Where-Object State -eq Ready | Select-Object -First 5 TaskName
Get-AppxPackage *spotify*
Get-LocalUser | Select-Object Name, Enabled
Get-Process | Where-Object { $_.MainWindowTitle } | Select-Object ProcessName, MainWindowTitle
cd $env:USERPROFILE; Get-ChildItem
Set-Location C:\; gci
Measure-Command { Get-ChildItem -Recurse }
Get-Random -Minimum 1 -Maximum 7
(Get-Item .\file.txt).Length
(Get-ChildItem).Where({ $_.PSIsContainer })
(Get-ChildItem).ForEach({ $_.Name })
Get-Process | Sort-Object { $_.CPU } -Descending
Write-Output 'hi'
Write-Host 'done' -ForegroundColor Green
'{0:N2} GB' -f ((Get-ChildItem -Recurse | Measure-Object Length -Sum).Sum / 1GB)
Get-Date -Format 'dddd, MMMM d'
(Get-Culture).DateTimeFormat.FirstDayOfWeek
Get-TimeZone
Select-String -Path *.txt -Pattern 'invoice' -List | Select-Object Path
Get-Counter '\Processor(_Total)\% Processor Time'
Get-NetTCPConnection -State Listen | Select-Object LocalPort, OwningProcess
Get-ChildItem Env:
Get-Process | % { $_.Name } -ErrorAction SilentlyContinue
(Get-Item .\a.txt).GetAccessControl().Owner
[regex]::Matches('a1b22c333', '\d+').Value
"$((Get-Date).Year - 1990) years"
# only a comment
""".strip().splitlines()

CHANGES = r"""
Get-ChildItem | Remove-Item
gci *.tmp | ri
Get-ChildItem -Recurse | ForEach-Object { Remove-Item $_.FullName }
Get-ChildItem | % { $_.Delete() }
ls | % Delete
ls | ForEach-Object -MemberName Delete
ls | % -Member Kill
(Get-ChildItem).ForEach('Delete')
Get-Process notepad | Stop-Process
Get-Process notepad | kill
(Get-Process notepad).Kill()
$p = Get-Process notepad; $p.Kill()
spps -Name notepad
Stop-Service Spooler
Start-Service Spooler
Restart-Service Spooler
Get-Service Spooler | Set-Service -StartupType Disabled
Start-Process notepad
start https://example.com
saps calc
Invoke-Item .\report.pdf
ii .
explorer .
notepad .\todo.txt
ipconfig /all
whoami
cmd /c dir
powershell -Command Get-Date
& 'C:\Program Files\App\app.exe'
& { Get-Date }
. { Get-Date }
. .\profile.ps1
.\script.ps1
C:\tools\cleanup.bat
iex 'Get-Date'
Invoke-Expression "Get-ChildItem"
Invoke-Command -ScriptBlock { Get-Date }
icm { Get-Date }
$sb = { Get-Date }; & $sb
$sb = { Get-Date }; $sb.Invoke()
[scriptblock]::Create('Get-Date').Invoke()
$ExecutionContext.InvokeCommand.InvokeScript('Get-Date')
Get-Date > now.txt
Get-Process >> procs.log
Get-ChildItem 2> errors.txt
Get-Process *> all.txt
Get-Process | Out-File procs.txt
Get-Process | Export-Csv procs.csv
Get-Process | Tee-Object -FilePath procs.txt
'hello' | Set-Content note.txt
'hello' | Add-Content note.txt
sc note.txt 'hello'
New-Item -ItemType File note.txt
ni note.txt
mkdir newfolder
md newfolder
Copy-Item a.txt b.txt
cp a.txt b.txt
Move-Item a.txt docs\
mv a.txt b.txt
Rename-Item a.txt b.txt
Get-ChildItem *.jpg | Rename-Item -NewName { $_.Name -replace 'IMG', 'Photo' }
Set-ItemProperty -Path HKCU:\Software\Test -Name X -Value 1
New-ItemProperty -Path HKCU:\Software\Test -Name X -Value 1
Set-Clipboard 'secret'
'text' | clip
Set-Date -Date '2026-01-01'
Set-TimeZone -Id 'UTC'
Set-NetIPInterface -InterfaceAlias Wi-Fi -Dhcp Enabled
Disable-NetAdapter -Name Wi-Fi
Enable-NetAdapter Ethernet
Invoke-WebRequest https://example.com
iwr https://example.com -OutFile x.html
curl https://example.com
wget https://example.com
Invoke-RestMethod https://api.example.com/items -Method Post -Body '{}'
Install-Module PSWindowsUpdate
Import-Module .\evil.psm1
Add-Type -TypeDefinition 'public class X {}'
New-Object -ComObject WScript.Shell
New-Object Net.WebClient
(New-Object Net.WebClient).DownloadString('https://example.com')
[IO.File]::WriteAllText('x.txt', 'hi')
[IO.File]::Delete('x.txt')
[System.IO.File]::AppendAllText('x.txt', 'hi')
[IO.Directory]::CreateDirectory('newdir')
[IO.Path]::GetTempFileName()
[Environment]::SetEnvironmentVariable('X', '1', 'User')
[Environment]::Exit(1)
[Diagnostics.Process]::Start('notepad')
[Activator]::CreateInstance([type]::GetTypeFromProgID('Shell.Application'))
[Reflection.Assembly]::LoadFile('C:\x.dll')
[Microsoft.Win32.Registry]::SetValue('HKEY_CURRENT_USER\x', 'y', 1)
$k = Get-Item HKCU:\Software; $k.SetValue('x', 1)
$t = [IO.File]; $t::Delete('x')
$env:PATH = 'C:\elsewhere'
${C:\temp\x.txt} = 'data'
$file = Get-Item a.txt; $file.IsReadOnly = $true
(Get-Item a.txt).LastWriteTime = Get-Date
$h = @{}; $h['a'] = 1
$host.UI.RawUI.WindowTitle = 'x'
[Console]::Title = 'x'
$x.Count++
function Get-Thing { Remove-Item x }; Get-Thing
function Get-Date { 'fake' }
class Foo { }
using module MyModule
#requires -Modules PSWindowsUpdate
Get-ChildItem @params
$params = @{Path='C:\'}; Get-ChildItem @params
Get-Help Get-ChildItem -Online
Get-Help Get-ChildItem -On
Get-Help about_Profiles -ShowWindow
help Get-ChildItem
Get-Process | Out-GridView
Get-Process | ogv
Start-Sleep 5
Read-Host 'name'
Get-WindowsUpdateLog
Get-WindowsUpdate -Install -AcceptAll
Get-Credential
Restart-Computer
Stop-Computer
shutdown /s /t 0
Clear-RecycleBin -Force
Remove-Item -Recurse -Force C:\temp\*
Get-ChildItem | Where-Object { Remove-Item $_ }
Get-ChildItem | Sort-Object { $_.Delete() }
Get-ChildItem | Select-Object @{n='x'; e={ Stop-Process -Id 1 }}
"$(Stop-Process -Name notepad)"
Write-Output "$(Remove-Item x)"
Get-ChildItem -Path (Remove-Item x)
$x = $(Stop-Process 1)
$m = 'Delete'; (Get-Item x).$m()
(Get-Item x).('De' + 'lete')()
(Get-Item x).'Delete'()
Get-Item x | ForEach-Object { $_.MoveTo('y') }
Get-Item x | % { $_.CopyTo('y') }
(Get-Item x).Replace('y', 'z')
Get-ChildItem | ForEach-Object -Process { $_ } -Begin { Stop-Process 1 }
Get-ChildItem | ForEach-Object -Process $block
Set-Alias Get-Date Remove-Item; Get-Date
New-Alias g Stop-Process
Get-Date }; Remove-Item x; {
Get-Date |
Get-ChildItem C:\ ; Remove-Item x
Get-Process | Get-Unique | Stop-Process
Start-Job { Get-Date }
Register-ScheduledTask -TaskName x
Set-ExecutionPolicy Bypass
Unblock-File .\x.ps1
Expand-Archive x.zip
Compress-Archive x y.zip
Get-Process | Out-Printer
Test-ComputerSecureChannel -Repair
Enter-PSSession server
Invoke-History 1
r 1
Update-Help
Get-Process | Format-Table | Out-File x.txt
1..3 | ForEach-Object { New-Item "f$_.txt" }
Get-Process | ForEach-Object { $_.CloseMainWindow() }
(Get-WmiObject Win32_OperatingSystem).Win32Shutdown(1)
Get-CimInstance Win32_Process | Invoke-CimMethod -MethodName Terminate
Microsoft.PowerShell.Management\Get-ChildItem
""".strip().splitlines()

SOURCES = ["Get-ChildItem ~/Documents", "gci -Recurse", "ls *.txt", r"dir C:\Users", "Get-Process", "ps", "Get-Service",
           "Get-Content notes.txt", "cat notes.txt", "Get-CimInstance Win32_LogicalDisk",
           r"Get-ItemProperty HKCU:\Software\X", "Get-NetIPAddress", "Get-Date", "1..20", "Import-Csv data.csv",
           "Get-WinEvent -LogName Application -MaxEvents 5", "Get-StartApps", "Get-ScheduledTask", "Get-Volume",
           "Get-AppxPackage"]
FILTERS = ["", "| Select-Object -First 5", "| sort Name", "| where Name -like 'a*'", "| ? { $_.Length -gt 1MB }",
           "| measure", "| Group-Object Extension", "| Format-Table -AutoSize", "| fl *", "| ConvertTo-Json -Depth 2",
           "| Out-String", "| % { $_.ToString().Trim() }", "| select -ExpandProperty Name",
           "| Sort-Object { $_.Name.Length } -Descending"]
SINKS = ["| Remove-Item", "| ri", "| del", "| Stop-Process", "| kill", "| Out-File out.txt", "| Set-Content x.txt",
         "| Export-Csv x.csv", "| Tee-Object -FilePath t.txt", "| % { $_.Delete() }", "| ForEach-Object Kill",
         "| Rename-Item -NewName x", r"| Move-Item -Destination D:\ ", r"| Copy-Item -Destination D:\ ", "> out.txt",
         ">> out.txt", "| Out-GridView", "| iex", "| clip", "| Set-Clipboard", "| % { & $_ }", "| Invoke-Item"]


def corpus() -> tuple[list[str], list[str]]:
    looks = LOOKS + [f"{s} {f}".strip() for s in SOURCES for f in FILTERS]
    changes = CHANGES + [f"{s} {k}" for s in SOURCES for k in SINKS]
    changes += [f"{s} {f} {k}" for s in SOURCES[::3] for f in FILTERS[1::3] for k in SINKS[::4]]
    return looks, changes


@pytest.fixture(scope="module")
def verdicts():
    looks, changes = corpus()
    lines = looks + changes
    return dict(zip(lines, terminal.classify_many(lines))), looks, changes


def test_the_corpus_is_big_enough():
    looks, changes = corpus()
    assert len(looks) >= 300 and len(changes) >= 500
    assert len(set(looks) & set(changes)) == 0


def test_commands_that_only_look(verdicts):
    judged, looks, _ = verdicts
    wrong = {line: judged[line].why for line in looks if not judged[line].looks}
    assert wrong == {}


def test_commands_that_change_something_never_count_as_looking(verdicts):
    judged, _, changes = verdicts
    assert [line for line in changes if judged[line].looks] == []
    assert all(judged[line].why for line in changes)


@pytest.mark.parametrize("line, why", [
    ("Get-ChildItem | Remove-Item", "Remove-Item"),
    ("gci | ri", "ri (Remove-Item)"),
    ("iex 'x'", "iex (Invoke-Expression)"),
    ("Get-Process > out.txt", "writes to a file"),
    ("& { Get-Date }", "& or ."),
    ("ls | % Name", "-MemberName"),
    ("(Get-Item x).Delete()", "Delete()"),
    ("[IO.File]::Delete('x')", "[IO.File]::Delete()"),
    ("$env:X = 1", "drive item"),
    ("$a.b = 1", "sets a property"),
    ("Get-Date }", "can't parse"),
    ("ipconfig", "ipconfig"),
    (r".\x.exe", "runs a program"),
    ("Get-ChildItem @p", "splat"),
    ("function f { }", "defines a function"),
])
def test_the_reason_names_what_changes(line, why):
    assert why in terminal.classify(line).why


# ------------------------------------------------------------------ the policy alone (no PowerShell)

def _facts(**kw):
    return {"errors": [], "commands": [], "members": [], "assigns": [], "redirects": [], "other": [], **kw}


def _cmd(name, resolved=None, **kw):
    return {"name": name, "resolved": resolved or name, "invocation": "Unknown", "bound": [], "nonblock": [],
            "splat": False, **kw}


@pytest.mark.parametrize("facts, looks", [
    (_facts(), True),
    (_facts(commands=[_cmd("Get-Date")]), True),
    (_facts(commands=[_cmd("get-date")]), True),                                   # any case
    (_facts(commands=[_cmd(None)]), False),                                       # chosen when it runs
    (_facts(commands=[_cmd("Get-Date", invocation="Ampersand")]), False),
    (_facts(commands=[_cmd("Get-Date", invocation="Dot")]), False),
    (_facts(commands=[_cmd("Get-Something-New")]), False),                       # unknown: asks
    (_facts(commands=[_cmd("Get-Date.exe")]), False),
    (_facts(commands=[_cmd("ForEach-Object", bound=None)]), False),               # couldn't bind: asks
    (_facts(commands=[_cmd("ForEach-Object", bound=["Process"])]), True),
    (_facts(commands=[_cmd("ForEach-Object", bound=["Process"], nonblock=["Process"])]), False),
    (_facts(commands=[_cmd("ForEach-Object", bound=["InputObject", "Process"])]), False),
    (_facts(members=[{"static": True, "type": "System.Math", "name": "Max"}]), True),
    (_facts(members=[{"static": True, "type": "Some.Other.Type", "name": "Parse"}]), False),
    (_facts(members=[{"static": True, "type": None, "name": "Parse"}]), False),
    (_facts(members=[{"static": False, "type": None, "name": "GetResponse"}]), False),
    (_facts(members=[{"static": False, "type": None, "name": "GetFiles"}]), True),
    (_facts(members=[{"static": False, "type": None, "name": None}]), False),
    (_facts(members=[{"static": False, "type": None, "name": "Where", "blocks": False}]), False),
    (_facts(redirects=["null"]), True),
    (_facts(redirects=["null", "file"]), False),
    (_facts(assigns=["variable"]), True),
    (_facts(assigns=["other"]), False),
    (_facts(other=["somethingnew"]), False),
    (_facts(errors=["Missing closing '}'"]), False),
])
def test_policy(facts, looks):
    assert terminal.judge(facts).looks is looks


def test_when_powershell_cant_check_it_asks(monkeypatch):
    def broken(*a, **kw):
        raise OSError("no powershell here")

    monkeypatch.setattr(terminal, "_powershell", broken)
    assert [v.looks for v in terminal.classify_many(["Get-Date", "Get-Process"])] == [False, False]


# ------------------------------------------------------------------ running, and what comes back

def test_plain_values_come_back_as_lines():
    ran = terminal.run("1..5", 30)
    assert ran.ok and ran.text == "1\n2\n3\n4\n5"


def test_objects_come_back_as_a_short_table():
    ran = terminal.run("[pscustomobject]@{Name='alpha'; Size=1}, [pscustomobject]@{Name='beta'; Size=22}", 30)
    lines = ran.text.splitlines()
    assert ran.ok and lines[0].split() == ["Name", "Size"] and set(lines[1]) <= {"-", " "}
    assert lines[2].split() == ["alpha", "1"] and lines[3].split() == ["beta", "22"]


def test_files_come_back_in_powershells_own_view(tmp_path):
    for name in ("a.txt", "b.txt"):
        (tmp_path / name).write_text("x" * 7)
    ran = terminal.run(f"Get-ChildItem '{tmp_path}' | Select-Object Name, Length", 30)
    assert ran.ok and "Name" in ran.text and "a.txt" in ran.text and "b.txt" in ran.text


def test_a_long_result_keeps_its_beginning_and_its_end():
    ran = terminal.run('1..400 | % { "row $_" }', 30, limit=1500)
    assert ran.ok and len(ran.text) <= 1500
    assert ran.text.startswith("row 1\nrow 2") and ran.text.endswith("row 400") and "330 more items" in ran.text


def test_long_lines_are_trimmed_with_a_note_never_silently():
    ran = terminal.run("1..60 | % { 'x' * 100 + $_ }", 30, limit=1500)
    assert ran.ok and len(ran.text) <= 1500 and "lines skipped" in ran.text
    assert ran.text.splitlines()[0].endswith("x1") and ran.text.splitlines()[-1].endswith("x60")


def test_the_same_error_about_many_things_is_one_line(tmp_path):
    paths = ", ".join(f"'{tmp_path / f'missing{i}'}'" for i in range(5))
    ran = terminal.run(f"Get-Item {paths}", 30)
    assert not ran.ok and "Errors (5):" in ran.text and "(and 4 like it)" in ran.text
    assert ran.text.count("Cannot find path") == 1


def test_output_with_some_errors_still_counts(tmp_path):
    (tmp_path / "here.txt").write_text("hi")
    ran = terminal.run(f"Get-Content '{tmp_path / 'here.txt'}', '{tmp_path / 'gone.txt'}'", 30)
    assert ran.ok and ran.text.startswith("hi") and "Errors (1):" in ran.text


def test_a_command_that_stops_says_why(tmp_path):
    ran = terminal.run(f"Get-Item '{tmp_path / 'nope'}' -ErrorAction Stop", 30)
    assert not ran.ok and ran.text.startswith("Failed: Cannot find path")


def test_exit_codes_count():
    assert terminal.run("cmd /c exit 3", 30).text == "Exit code 3."
    ran = terminal.run("exit 4", 30)
    assert not ran.ok and "Exit code 4." in ran.text


def test_text_survives_any_encoding_and_quoting():
    ran = terminal.run("'é ü ✓ “quoted” \"double\" $not_a_variable' + \" `$escaped\"", 30)
    assert ran.ok and ran.text == 'é ü ✓ “quoted” "double" $not_a_variable $escaped'


def test_host_lines_and_warnings():
    ran = terminal.run("Write-Host hello; Write-Warning careful; 'done'", 30)
    assert ran.ok and ran.text == "hello\ndone\nWarnings: careful"


def test_nothing_printed():
    assert terminal.run("$null", 30).text == "(no output)"


def test_a_slow_command_times_out():
    ran = terminal.run("Start-Sleep 10", 1)
    assert not ran.ok and ran.text == "Timed out after 1 s."


@pytest.mark.parametrize("text, room", [("a\n" * 500, 200), ("x" * 5000, 300), ("\n".join(map(str, range(999))), 400)])
def test_trim_keeps_within_room(text, room):
    out = terminal.trim(text, room)
    assert len(out) <= room + 40 and ("skipped" in out or out.endswith("…"))


def test_tidy():
    assert terminal.tidy("\n\n a  \r\n\r\n\r\n b\n\n") == " a\n\n b"


# ------------------------------------------------------------------ through the registry: ask or not

class Asked(Output):
    def __init__(self):
        self.questions = []

    def confirm(self, question):
        self.questions.append(question)
        return False   # never let a changing command run in a test


@pytest.fixture
def registry():
    cfg = load_config()
    cfg["skills"] = ["powershell"]
    out = Asked()
    return ToolRegistry(ToolContext(cfg, None, out)), out


def test_a_command_that_only_looks_runs_without_asking(registry):
    reg, out = registry
    r = reg.run("run_powershell", {"command": "[math]::Round(22 / 7, 3)"})
    assert r.ok and r.text == "3.143" and out.questions == []


def test_a_command_that_changes_something_asks_first(registry):
    reg, out = registry
    r = reg.run("run_powershell", {"command": "New-Item -ItemType Directory mary-test-should-not-exist"})
    assert not r.ok and "said no" in r.text
    assert out.questions == ["I'm about to run this PowerShell command: New-Item -ItemType Directory "
                             "mary-test-should-not-exist. Shall I go ahead?"]


def test_the_blocklist_still_refuses(registry):
    reg, out = registry
    r = reg.run("run_powershell", {"command": "Get-ChildItem | Remove-Item"})
    assert not r.ok and "Refused for safety" in r.text and out.questions == []


def test_only_changing_commands_are_watched(registry):
    reg, _ = registry
    tool = reg.tools["run_powershell"]
    assert tool.watch({"command": "Get-Date"}) is False
    assert tool.watch({"command": "Start-Process notepad"}) is True
    assert tool.tier_for({"command": "Get-Date"}) == 1 and tool.tier_for({"command": "Start-Process notepad"}) == 2
