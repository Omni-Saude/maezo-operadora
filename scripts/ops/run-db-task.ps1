# Roda um script Python DENTRO da VPC usando a task definition maezo-operadora-dev-bootstrap-db
# (que carrega a credencial mestre amh_admin via `secrets`). O script viaja base64 no
# containerOverrides.command; nada e' escrito em disco (raiz somente-leitura).
#
# Uso: .\run-db-task.ps1 -Script <caminho.py> [-ExtraEnv @{CHAVE='valor'}]
param(
  [Parameter(Mandatory = $true)][string]$Script,
  [hashtable]$ExtraEnv = @{},
  [string]$SecurityGroup = 'sg-0e2aebe1a2c1d253d'
)

$ErrorActionPreference = 'Stop'
$env:AWS_PROFILE = 'adm-dev'

$cluster = 'maezo-operadora-dev'
$family  = 'maezo-operadora-dev-bootstrap-db'
$container = 'bootstrap-db'
$logGroup = '/ecs/maezo-operadora-dev/bootstrap-db'
$subnets = @('subnet-06647b0c664d0d22a', 'subnet-0e1f840dbec44e66a')
$sg = $SecurityGroup

# gzip+base64: o containerOverrides do RunTask tem teto de 8192 bytes.
$srcBytes = [IO.File]::ReadAllBytes($Script)
$ms = New-Object IO.MemoryStream
$gz = New-Object IO.Compression.GZipStream($ms, [IO.Compression.CompressionLevel]::Optimal)
$gz.Write($srcBytes, 0, $srcBytes.Length)
$gz.Dispose()
$b64 = [Convert]::ToBase64String($ms.ToArray())
$ms.Dispose()
Write-Host "script: $($srcBytes.Length) bytes -> payload base64 $($b64.Length) chars"
$envList = @()
foreach ($k in $ExtraEnv.Keys) { $envList += @{ name = $k; value = [string]$ExtraEnv[$k] } }

$overrides = @{
  containerOverrides = @(@{
      name        = $container
      command     = @('python', '-c', "import base64,gzip;exec(compile(gzip.decompress(base64.b64decode('$b64')),'<vpc>','exec'))")
      environment = $envList
    })
}

$netCfg = @{
  awsvpcConfiguration = @{
    subnets        = $subnets
    securityGroups = @($sg)
    assignPublicIp = 'DISABLED'
  }
}

$ovFile = Join-Path $env:TEMP "ov-$([guid]::NewGuid().ToString('N')).json"
$netFile = Join-Path $env:TEMP "net-$([guid]::NewGuid().ToString('N')).json"
$utf8NoBom = New-Object System.Text.UTF8Encoding($false)
[IO.File]::WriteAllText($ovFile, ($overrides | ConvertTo-Json -Depth 8 -Compress), $utf8NoBom)
[IO.File]::WriteAllText($netFile, ($netCfg | ConvertTo-Json -Depth 8 -Compress), $utf8NoBom)

try {
  $raw = aws ecs run-task --cluster $cluster --task-definition $family --launch-type FARGATE `
    --region sa-east-1 --network-configuration "file://$netFile" --overrides "file://$ovFile" `
    --output json
  $task = ($raw | ConvertFrom-Json).tasks[0]
  if (-not $task) { Write-Host "run-task nao devolveu task:"; Write-Host $raw; exit 1 }
  $arn = $task.taskArn
  $id = $arn.Split('/')[-1]
  Write-Host "task: $id"

  aws ecs wait tasks-stopped --cluster $cluster --tasks $arn --region sa-east-1
  $done = (aws ecs describe-tasks --cluster $cluster --tasks $arn --region sa-east-1 --output json | ConvertFrom-Json).tasks[0]
  $c = $done.containers[0]
  Write-Host "exitCode: $($c.exitCode)  reason: $($c.reason)  stopped: $($done.stoppedReason)"

  $stream = "bootstrap/$container/$id"
  $ev = aws logs get-log-events --log-group-name $logGroup --log-stream-name $stream `
    --region sa-east-1 --start-from-head --output json | ConvertFrom-Json
  Write-Host "----- LOG ($stream) -----"
  foreach ($e in $ev.events) { Write-Host $e.message }
  Write-Host "----- FIM LOG -----"
  exit [int]$c.exitCode
}
finally {
  Remove-Item $ovFile, $netFile -ErrorAction SilentlyContinue
}
