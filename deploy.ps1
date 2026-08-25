# Deploy export_idml plugin to QGIS3 + QGIS4 default profiles.
$src = Join-Path $PSScriptRoot "export_idml"
$targets = @(
    "$env:APPDATA\QGIS\QGIS3\profiles\default\python\plugins\export_idml",
    "$env:APPDATA\QGIS\QGIS4\profiles\default\python\plugins\export_idml"
)
foreach ($dst in $targets) {
    if (Test-Path $dst) { Remove-Item -Recurse -Force $dst }
    Copy-Item -Recurse $src $dst
    # strip caches
    Get-ChildItem $dst -Recurse -Directory -Filter "__pycache__" |
        Remove-Item -Recurse -Force
    Write-Output "deployed -> $dst"
}
Write-Output "Enable via Plugins > Manage and Install Plugins > 'Export IDML' (restart QGIS or use Plugin Reloader)."
