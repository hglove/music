Add-Type -AssemblyName System.Runtime.WindowsRuntime | Out-Null
$null = [Windows.Media.Control.GlobalSystemMediaTransportControlsSessionManager, Windows.Media.Control, ContentType = WindowsRuntime]
$op = [Windows.Media.Control.GlobalSystemMediaTransportControlsSessionManager]::RequestAsync()
Write-Output ("op type: " + $op.GetType().FullName)
$ga = $op.GetType().GetGenericArguments()
Write-Output ("generic args: " + (($ga | ForEach-Object { $_.FullName }) -join ", "))
$ext = [System.WindowsRuntimeSystemExtensions]
$m = $null
foreach ($x in $ext.GetMethods()) {
  if ($x.Name -ne 'AsTask') { continue }
  $pp = $x.GetParameters()
  if ($pp.Count -ne 1) { continue }
  if (-not $pp[0].ParameterType.IsGenericType) { continue }
  if ($pp[0].ParameterType.GetGenericTypeDefinition().Name -notlike 'IAsyncOperation*') { continue }
  $m = $x
  break
}
Write-Output ("method found: " + ($null -ne $m))
try {
  $closed = $m.MakeGenericMethod($ga[0])
  Write-Output ("closed: " + $closed.ToString())
  $arr = New-Object object[] 1
  $arr[0] = $op
  $task = $closed.Invoke($null, $arr)
  Write-Output ("task type: " + $task.GetType().FullName)
  $ok = $task.Wait(8000)
  Write-Output ("wait returned: " + $ok + " faulted: " + $task.IsFaulted)
  if ($task.IsFaulted) {
    Write-Output ("task exception: " + $task.Exception.InnerException.Message)
  } else {
    $mgr = $task.Result
    Write-Output ("mgr is null: " + ($null -eq $mgr))
    if ($null -ne $mgr) {
      $s = $mgr.GetCurrentSession()
      Write-Output ("session is null: " + ($null -eq $s))
    }
  }
} catch {
  $es = $_.Exception.ToString()
  Write-Output ("REFLECT ERROR: " + $es.Substring(0, [Math]::Min(600, $es.Length)))
}