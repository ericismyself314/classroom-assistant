param(
    [string]$ProjectRoot = (Split-Path -Parent $PSScriptRoot)
)

$ErrorActionPreference = "Stop"
Add-Type -AssemblyName System.Drawing

$assetsDir = Join-Path $ProjectRoot "assets"
$icoPath = Join-Path $ProjectRoot "课堂助手.ico"
$previewPath = Join-Path $assetsDir "classroom-assistant-icon.png"
New-Item -ItemType Directory -Path $assetsDir -Force | Out-Null

function New-IconBitmap([int]$size) {
    $scale = [double]$size / 256.0
    function X([double]$value) { return [single]($value * $scale) }
    $bmp = New-Object System.Drawing.Bitmap($size, $size, [System.Drawing.Imaging.PixelFormat]::Format32bppArgb)
    $g = [System.Drawing.Graphics]::FromImage($bmp)
    $g.SmoothingMode = [System.Drawing.Drawing2D.SmoothingMode]::AntiAlias
    $g.InterpolationMode = [System.Drawing.Drawing2D.InterpolationMode]::HighQualityBicubic
    $g.PixelOffsetMode = [System.Drawing.Drawing2D.PixelOffsetMode]::HighQuality
    $g.Clear([System.Drawing.Color]::Transparent)

    $shadow = New-Object System.Drawing.Drawing2D.GraphicsPath
    $shadowRadius = X 54
    $shadowX = X 12
    $shadowY = X 19
    $shadowW = X 232
    $shadowH = X 232
    $null = $shadow.AddArc($shadowX, $shadowY, $shadowRadius * 2, $shadowRadius * 2, 180, 90)
    $null = $shadow.AddArc($shadowX + $shadowW - $shadowRadius * 2, $shadowY, $shadowRadius * 2, $shadowRadius * 2, 270, 90)
    $null = $shadow.AddArc($shadowX + $shadowW - $shadowRadius * 2, $shadowY + $shadowH - $shadowRadius * 2, $shadowRadius * 2, $shadowRadius * 2, 0, 90)
    $null = $shadow.AddArc($shadowX, $shadowY + $shadowH - $shadowRadius * 2, $shadowRadius * 2, $shadowRadius * 2, 90, 90)
    $null = $shadow.CloseFigure()
    $shadowBrush = New-Object System.Drawing.SolidBrush([System.Drawing.Color]::FromArgb(55, 20, 58, 38))
    $g.FillPath($shadowBrush, $shadow)

    $bg = New-Object System.Drawing.Drawing2D.GraphicsPath
    $radius = X 54
    $boxX = X 12
    $boxY = X 12
    $boxW = X 232
    $boxH = X 232
    $null = $bg.AddArc($boxX, $boxY, $radius * 2, $radius * 2, 180, 90)
    $null = $bg.AddArc($boxX + $boxW - $radius * 2, $boxY, $radius * 2, $radius * 2, 270, 90)
    $null = $bg.AddArc($boxX + $boxW - $radius * 2, $boxY + $boxH - $radius * 2, $radius * 2, $radius * 2, 0, 90)
    $null = $bg.AddArc($boxX, $boxY + $boxH - $radius * 2, $radius * 2, $radius * 2, 90, 90)
    $null = $bg.CloseFigure()
    $gradientRect = New-Object System.Drawing.RectangleF($boxX, $boxY, $boxW, $boxH)
    $gradient = New-Object System.Drawing.Drawing2D.LinearGradientBrush($gradientRect, [System.Drawing.Color]::FromArgb(255, 59, 148, 96), [System.Drawing.Color]::FromArgb(255, 36, 98, 63), 45)
    $g.FillPath($gradient, $bg)
    $rim = New-Object System.Drawing.Pen([System.Drawing.Color]::FromArgb(42, 255, 255, 255), (X 3))
    $g.DrawPath($rim, $bg)

    $left = [System.Drawing.PointF[]]@(
        [System.Drawing.PointF]::new((X 44), (X 78)),
        [System.Drawing.PointF]::new((X 80), (X 72)),
        [System.Drawing.PointF]::new((X 128), (X 96)),
        [System.Drawing.PointF]::new((X 128), (X 200)),
        [System.Drawing.PointF]::new((X 86), (X 180)),
        [System.Drawing.PointF]::new((X 44), (X 186))
    )
    $right = [System.Drawing.PointF[]]@(
        [System.Drawing.PointF]::new((X 128), (X 96)),
        [System.Drawing.PointF]::new((X 176), (X 72)),
        [System.Drawing.PointF]::new((X 212), (X 78)),
        [System.Drawing.PointF]::new((X 212), (X 186)),
        [System.Drawing.PointF]::new((X 170), (X 180)),
        [System.Drawing.PointF]::new((X 128), (X 200))
    )
    $bookLeft = New-Object System.Drawing.SolidBrush([System.Drawing.Color]::FromArgb(255, 249, 255, 251))
    $bookRight = New-Object System.Drawing.SolidBrush([System.Drawing.Color]::FromArgb(255, 231, 244, 235))
    $g.FillPolygon($bookLeft, $left)
    $g.FillPolygon($bookRight, $right)

    $crease = New-Object System.Drawing.Pen([System.Drawing.Color]::FromArgb(140, 36, 98, 63), (X 5))
    $crease.StartCap = [System.Drawing.Drawing2D.LineCap]::Round
    $crease.EndCap = [System.Drawing.Drawing2D.LineCap]::Round
    $g.DrawLine($crease, (X 128), (X 96), (X 128), (X 200))
    $lines = New-Object System.Drawing.Pen([System.Drawing.Color]::FromArgb(185, 185, 216, 195), (X 5))
    $lines.StartCap = [System.Drawing.Drawing2D.LineCap]::Round
    $lines.EndCap = [System.Drawing.Drawing2D.LineCap]::Round
    foreach ($y in @(99, 122, 145)) {
        $g.DrawLine($lines, (X 57), (X $y), (X 116), (X ($y + 12)))
        $g.DrawLine($lines, (X 199), (X $y), (X 140), (X ($y + 12)))
    }

    $signal = New-Object System.Drawing.SolidBrush([System.Drawing.Color]::FromArgb(255, 243, 179, 91))
    $g.FillEllipse($signal, (X 171), (X 38), (X 38), (X 38))
    $bars = New-Object System.Drawing.SolidBrush([System.Drawing.Color]::FromArgb(255, 255, 250, 240))
    foreach ($bar in @(@(182, 48, 5, 18), @(190, 43, 5, 28), @(198, 50, 5, 14))) {
        $g.FillRoundedRectangle($bars, (X $bar[0]), (X $bar[1]), (X $bar[2]), (X $bar[3]), (X 2.5))
    }

    foreach ($obj in @($shadowBrush, $shadow, $gradient, $bg, $rim, $bookLeft, $bookRight, $crease, $lines, $signal, $bars, $g)) {
        if ($obj -and $obj -is [System.IDisposable]) { $obj.Dispose() }
    }
    return $bmp
}

# Graphics.FillRoundedRectangle is not available on every System.Drawing build;
# add a small helper extension-like function for the three signal bars.
Update-TypeData -TypeName System.Drawing.Graphics -MemberName FillRoundedRectangle -MemberType ScriptMethod -Value {
    param($brush, $x, $y, $width, $height, $radius)
    $path = New-Object System.Drawing.Drawing2D.GraphicsPath
    $path.AddArc($x, $y, $radius * 2, $radius * 2, 180, 90)
    $path.AddArc($x + $width - $radius * 2, $y, $radius * 2, $radius * 2, 270, 90)
    $path.AddArc($x + $width - $radius * 2, $y + $height - $radius * 2, $radius * 2, $radius * 2, 0, 90)
    $path.AddArc($x, $y + $height - $radius * 2, $radius * 2, $radius * 2, 90, 90)
    $path.CloseFigure()
    $this.FillPath($brush, $path)
    $path.Dispose()
} -Force

$sizes = @(16, 24, 32, 48, 64, 128, 256)
$pngs = @()
foreach ($size in $sizes) {
    $bmp = New-IconBitmap $size
    $stream = New-Object System.IO.MemoryStream
    $bmp.Save($stream, [System.Drawing.Imaging.ImageFormat]::Png)
    $pngs += ,$stream.ToArray()
    if ($size -eq 256) {
        [System.IO.File]::WriteAllBytes($previewPath, $stream.ToArray())
    }
    $stream.Dispose()
    $bmp.Dispose()
}

$ico = New-Object System.IO.MemoryStream
$writer = New-Object System.IO.BinaryWriter($ico)
$writer.Write([uint16]0)
$writer.Write([uint16]1)
$writer.Write([uint16]$pngs.Count)
$offset = 6 + (16 * $pngs.Count)
for ($i = 0; $i -lt $pngs.Count; $i++) {
    $size = $sizes[$i]
    $writer.Write([byte]$(if ($size -ge 256) { 0 } else { $size }))
    $writer.Write([byte]$(if ($size -ge 256) { 0 } else { $size }))
    $writer.Write([byte]0)
    $writer.Write([byte]0)
    $writer.Write([uint16]1)
    $writer.Write([uint16]32)
    $writer.Write([uint32]$pngs[$i].Length)
    $writer.Write([uint32]$offset)
    $offset += $pngs[$i].Length
}
foreach ($png in $pngs) { $writer.Write($png) }
$writer.Flush()
[System.IO.File]::WriteAllBytes($icoPath, $ico.ToArray())
$writer.Dispose()
$ico.Dispose()

Write-Host "Icon generated: $icoPath"
Write-Host "Preview generated: $previewPath"
