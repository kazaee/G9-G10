$ErrorActionPreference = 'Stop'

$disk = Get-Disk -Number 1
$systemDisk = (Get-Partition -DriveLetter C).DiskNumber
$parts = @(Get-Partition -DiskNumber 1)
$expectedSize = [int64]4000753475584
$sizeTolerance = 64MB

if ($disk.FriendlyName -ne 'HP LOGICAL VOLUME' -or
    [Math]::Abs([int64]$disk.Size - $expectedSize) -gt $sizeTolerance -or
    $disk.PartitionStyle -ne 'GPT' -or
    $disk.OperationalStatus -ne 'Online' -or
    $disk.IsBoot -or $disk.IsSystem -or $systemDisk -eq 1 -or
    $parts.Count -ne 1 -or $parts[0].Type -ne 'Unknown' -or
    $parts[0].Size -lt 3TB) {
    throw 'Disk 1 no longer matches the reviewed 4 TB VMFS data disk. No changes made.'
}
if (Get-Volume -DriveLetter F -ErrorAction SilentlyContinue) {
    throw 'Drive letter F is in use. No changes made.'
}

Write-Host 'Disk 1: 4 TB HP LOGICAL VOLUME, one Unknown/VMFS partition.'
Write-Host 'All existing contents of this disk will be erased and replaced by one NTFS F: volume.'

Clear-Disk -Number 1 -RemoveData -Confirm:$false
Initialize-Disk -Number 1 -PartitionStyle GPT
New-Partition -DiskNumber 1 -UseMaximumSize -DriveLetter F | Out-Null
Format-Volume -DriveLetter F -FileSystem NTFS -NewFileSystemLabel Data -Confirm:$false -Force | Out-Null

$volume = Get-Volume -DriveLetter F
if ($volume.FileSystem -ne 'NTFS' -or $volume.Size -lt 3TB) {
    throw 'Data volume verification failed. Inspect Disk Management.'
}
Write-Host "Data disk ready: F: $($volume.FileSystem), $([Math]::Round($volume.Size / 1TB, 2)) TiB"
