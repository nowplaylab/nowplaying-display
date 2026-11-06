# Поставить самую свежую версию NowPlaying сразу, не дожидаясь ежедневного выпуска.
# Берёт и уже вышедшие версии, и черновики в очереди (их видит только владелец репозитория:
# скрипт берёт вход в GitHub, который сохранил git на этом компьютере).
# Новый exe ставится на место запущенного, APK кладётся туда, откуда его заберут
# планшет и телевизор (они обновятся сами в течение пары минут).
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
$Repo = 'nowplaylab/nowplaying-display'
$DataDir = Join-Path $env:LOCALAPPDATA 'NowPlaying'
$UpdDir = Join-Path $DataDir 'update'
$Check = $args -contains '-check'     # только показать, что поставится, ничего не меняя
$DownloadOnly = $args -contains '-download'   # скачать и проверить, но не ставить

function Say($t, $c = 'Gray') { Write-Host $t -ForegroundColor $c }
function Ver($v) { [int[]]([regex]::Matches($v, '\d+') | ForEach-Object { $_.Value }) }
function Newer($a, $b) {   # a > b по номерам версии
    $x = Ver $a; $y = Ver $b
    for ($i = 0; $i -lt [Math]::Max($x.Count, $y.Count); $i++) {
        $p = if ($i -lt $x.Count) { $x[$i] } else { 0 }; $q = if ($i -lt $y.Count) { $y[$i] } else { 0 }
        if ($p -ne $q) { return $p -gt $q }
    }
    return $false
}

# --- вход в GitHub из git
$token = $null
try {
    $env:GCM_INTERACTIVE = 'never'
    $cred = "protocol=https`nhost=github.com`n`n" | git credential fill 2>$null
    $token = ($cred | Where-Object { $_ -like 'password=*' } | Select-Object -First 1) -replace '^password=', ''
} catch { }
$h = @{ 'Accept' = 'application/vnd.github+json'; 'User-Agent' = 'NowPlaying-updater' }
if ($token) { $h['Authorization'] = "Bearer $token" }
else { Say 'Вход в GitHub не найден — смотрю только вышедшие версии, без черновиков.' Yellow }

# --- самая свежая версия, в которой есть программа и контрольные суммы
$rels = Invoke-RestMethod "https://api.github.com/repos/$Repo/releases?per_page=30" -Headers $h
$best = $null
foreach ($r in $rels) {
    $names = @($r.assets | ForEach-Object { $_.name })
    if ($names -notcontains 'NowPlayingServer.exe' -or $names -notcontains 'SHA256SUMS.txt') { continue }
    $v = $r.tag_name.TrimStart('v')
    if (-not $best -or (Newer $v $best.v)) { $best = @{ v = $v; r = $r } }
}
if (-not $best) { Say 'Не нашёл ни одной версии на GitHub.' Red; exit 1 }
$v = $best.v; $rel = $best.r
$kind = if ($rel.draft) { 'черновик, ещё не вышла для всех' } else { 'уже вышла' }

# --- что стоит сейчас
$proc = Get-Process NowPlayingServer -ErrorAction SilentlyContinue | Where-Object { $_.Path } | Select-Object -First 1
# ставим всегда в постоянное место, даже если сейчас программа запущена откуда-то ещё
$exePath = Join-Path $DataDir 'NowPlayingServer.exe'
if ($proc -and $proc.Path -ne $exePath) { Say "Сейчас программа запущена из $($proc.Path) — перенесу её в постоянное место." Yellow }
Say "Самая свежая версия: $v ($kind)" White
Say "Программа на компьютере: $exePath"
if ($Check) { Say 'Проверка: ничего не меняю.' Yellow; exit 0 }

# --- скачать и сверить
New-Item -ItemType Directory -Force $UpdDir | Out-Null
function Fetch($asset, $dest) {
    # файл релиза отдаётся через переадресацию на другой сервер: туда идём уже без входа,
    # иначе он отказывает (PowerShell 5 сам пересылает вход дальше)
    $req = [Net.HttpWebRequest]::Create($asset.url)
    $req.Accept = 'application/octet-stream'; $req.UserAgent = 'NowPlaying-updater'; $req.AllowAutoRedirect = $false
    if ($token) { $req.Headers['Authorization'] = "Bearer $token" }
    $resp = $req.GetResponse()
    $url = $resp.Headers['Location']; $resp.Close()
    if (-not $url) { $url = $asset.browser_download_url }
    Invoke-WebRequest $url -OutFile $dest -UseBasicParsing -UserAgent 'NowPlaying-updater'
}
$sumsFile = Join-Path $env:TEMP "np-sums-$v.txt"
Fetch ($rel.assets | Where-Object name -eq 'SHA256SUMS.txt') $sumsFile
$sums = @{}
foreach ($line in Get-Content $sumsFile) {
    if ($line -match '^([0-9a-fA-F]{64})\s+\*?(\S+)') { $sums[$Matches[2]] = $Matches[1].ToLower() }
}
function Get-Checked($name, $dest) {
    Say "Скачиваю $name…"
    Fetch ($rel.assets | Where-Object name -eq $name) $dest
    $got = (Get-FileHash $dest -Algorithm SHA256).Hash.ToLower()
    if ($got -ne $sums[$name]) { Remove-Item $dest -Force; throw "$name скачался с ошибкой (не сошлась контрольная сумма)" }
}

$newExe = Join-Path $UpdDir "NowPlayingServer-$v.manual.exe"
Get-Checked 'NowPlayingServer.exe' $newExe
$apk = $rel.assets | Where-Object { $_.name -match '^NowPlaying-.+\.apk$' } | Select-Object -First 1
if ($apk) { Get-Checked $apk.name (Join-Path $UpdDir $apk.name) }

if ($DownloadOnly) { Say "Скачано и проверено, не ставлю: $UpdDir" Yellow; Remove-Item $newExe -Force; exit 0 }

# --- подменить программу и запустить заново
if ($proc) {
    Say 'Закрываю старую программу…'
    Get-Process NowPlayingServer -ErrorAction SilentlyContinue | Stop-Process -Force
    for ($i = 0; $i -lt 20 -and (Get-Process NowPlayingServer -ErrorAction SilentlyContinue); $i++) { Start-Sleep -Milliseconds 300 }
    Start-Sleep -Milliseconds 500
}
Copy-Item $newExe $exePath -Force
Remove-Item $newExe -Force

# автозапуск должен запускать именно этот файл
$task = Get-ScheduledTask -TaskName 'NowPlaying Server' -ErrorAction SilentlyContinue
if ($task -and $task.Actions[0].Execute -ne $exePath) {
    try {
        Set-ScheduledTask -TaskName 'NowPlaying Server' -Action (New-ScheduledTaskAction -Execute $exePath) | Out-Null
        Say 'Автозапуск теперь запускает программу из постоянного места.'
    } catch {
        Say 'Не смог поправить автозапуск. Выключи и снова включи «Запускать вместе с Windows» в меню значка у часов.' Yellow
    }
}
Start-Process $exePath
Say ''
Say "Готово: стоит NowPlaying $v." Green
if ($apk) { Say "Планшет и телевизор сами возьмут приложение $($apk.name -replace '^NowPlaying-|\.apk$') с компьютера в течение пары минут." Green }
