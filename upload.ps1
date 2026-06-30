# upload.ps1 — заливает на виртуалку только важные файлы проекта.
# Запуск из любой папки:
#   powershell -ExecutionPolicy Bypass -File C:\Users\Oleg\Projects\tg-vk-bridge\upload.ps1
$ErrorActionPreference = 'Stop'

$Remote    = 'root@78.17.114.254'
$RemoteDir = '/root/tg-vk-bridge'

# файлы проекта лежат рядом с этим скриптом — считаем пути от него
$Root = $PSScriptRoot

# Только важное: код + конфиг + докер + .env + сам run.sh.
# НЕ заливаем: store.json (живёт в docker-volume на сервере), *.session,
# login.py (вход делается локально), тесты, README, __pycache__, .git.
$Files = @(
    'bridge.py',
    'config.py',
    'vk.py',
    'store.py',
    'reactions.py',
    'requirements.txt',
    'Dockerfile',
    'docker-compose.yml',
    '.env',
    'run.sh'
)

# проверяем, что всё на месте, и собираем абсолютные пути
$Paths = foreach ($f in $Files) {
    $p = Join-Path $Root $f
    if (-not (Test-Path $p)) { throw "Нет файла: $p" }
    $p
}

Write-Host "→ создаю $RemoteDir на $Remote"
ssh $Remote "mkdir -p $RemoteDir"

Write-Host "→ заливаю $($Paths.Count) файлов в ${Remote}:${RemoteDir}/"
scp $Paths "${Remote}:${RemoteDir}/"

# нормализуем переводы строк run.sh (на случай CRLF из Windows) и делаем исполняемым
Write-Host "→ готовлю run.sh на сервере"
ssh $Remote "sed -i 's/\r`$//' $RemoteDir/run.sh && chmod +x $RemoteDir/run.sh"

Write-Host ""
Write-Host "Готово. Запуск на сервере:" -ForegroundColor Green
Write-Host "  ssh $Remote $RemoteDir/run.sh"
