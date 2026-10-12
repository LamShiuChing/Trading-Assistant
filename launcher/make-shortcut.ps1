# Creates launcher\icon.ico and a "Trading Assistant" shortcut on the Desktop. Right-click it and choose "Pin to taskbar".
$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Drawing
$ico = Join-Path $PSScriptRoot 'icon.ico'

# Draw a 256x256 icon: three candles on a dark rounded square.
$bmp = New-Object Drawing.Bitmap 256, 256
$g = [Drawing.Graphics]::FromImage($bmp)
$g.SmoothingMode = 'AntiAlias'
$path = New-Object Drawing.Drawing2D.GraphicsPath
$r = 48
$path.AddArc(0, 0, $r, $r, 180, 90); $path.AddArc(256 - $r, 0, $r, $r, 270, 90)
$path.AddArc(256 - $r, 256 - $r, $r, $r, 0, 90); $path.AddArc(0, 256 - $r, $r, $r, 90, 90)
$g.FillPath((New-Object Drawing.SolidBrush ([Drawing.Color]::FromArgb(18, 22, 28))), $path)
$wick = New-Object Drawing.Pen ([Drawing.Color]::FromArgb(138, 145, 156)), 10
$green = New-Object Drawing.SolidBrush ([Drawing.Color]::FromArgb(34, 197, 94))
$red = New-Object Drawing.SolidBrush ([Drawing.Color]::FromArgb(239, 68, 68))
foreach ($c in @(@(72, 48, 208, 88, 160, $green), @(128, 72, 208, 104, 160, $red), @(184, 32, 176, 56, 144, $green))) {
    $g.DrawLine($wick, $c[0], $c[1], $c[0], $c[2])
    $g.FillRectangle($c[5], $c[0] - 24, $c[3], 48, $c[4] - $c[3])
}
$ms = New-Object IO.MemoryStream
$bmp.Save($ms, [Drawing.Imaging.ImageFormat]::Png)
$png = $ms.ToArray()

# Write an .ico with a single PNG image (supported since Windows Vista).
$w = New-Object IO.BinaryWriter ([IO.File]::Create($ico))
$w.Write([UInt16]0); $w.Write([UInt16]1); $w.Write([UInt16]1)          # header: reserved, type=icon, count=1
$w.Write([Byte]0); $w.Write([Byte]0); $w.Write([Byte]0); $w.Write([Byte]0)  # 256x256, no palette
$w.Write([UInt16]1); $w.Write([UInt16]32); $w.Write([UInt32]$png.Length); $w.Write([UInt32]22)
$w.Write($png)
$w.Close()

$lnk = Join-Path ([Environment]::GetFolderPath('Desktop')) 'Trading Assistant.lnk'
$s = (New-Object -ComObject WScript.Shell).CreateShortcut($lnk)
$s.TargetPath = "$env:SystemRoot\System32\WindowsPowerShell\v1.0\powershell.exe"
$s.Arguments = "-NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File `"$PSScriptRoot\TradingAssistant.ps1`""
$s.WorkingDirectory = Split-Path $PSScriptRoot -Parent
$s.IconLocation = "$ico,0"
$s.WindowStyle = 7  # minimized, so the PowerShell console doesn't flash
$s.Save()
"Created $lnk. Right-click it and choose 'Pin to taskbar'."
