# Prints every text line the built-in Windows OCR finds in each image:
#   <index>`t<x>`t<y>`t<text>    (index = position of the image in the argument list,
#                                  x, y = centre of the line in pixels, origin top-left)
# Used to read the apartment type printed on floor plans. An image that fails is skipped
# (reported on stderr) so one bad file never stops the others. Exit 3 = no OCR engine.
# Runs under Windows PowerShell 5.1 (powershell.exe), which can load WinRT types.
param(
    [Parameter(ValueFromRemainingArguments = $true)][string[]]$Paths
)
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = New-Object System.Text.UTF8Encoding $false   # no BOM

Add-Type -AssemblyName System.Runtime.WindowsRuntime
$null = [Windows.Media.Ocr.OcrEngine, Windows.Foundation, ContentType = WindowsRuntime]
$null = [Windows.Globalization.Language, Windows.Foundation, ContentType = WindowsRuntime]
$null = [Windows.Storage.StorageFile, Windows.Storage, ContentType = WindowsRuntime]
$null = [Windows.Storage.Streams.IRandomAccessStream, Windows.Storage.Streams, ContentType = WindowsRuntime]
$null = [Windows.Graphics.Imaging.BitmapDecoder, Windows.Graphics, ContentType = WindowsRuntime]

# WinRT async calls are awaited through .NET's AsTask(IAsyncOperation<T>)
$asTask = [System.WindowsRuntimeSystemExtensions].GetMethods() | Where-Object {
    $_.Name -eq 'AsTask' -and $_.GetParameters().Count -eq 1 -and
    $_.GetParameters()[0].ParameterType.Name -eq 'IAsyncOperation`1'
} | Select-Object -First 1

function Await($operation, [Type]$resultType) {
    $task = $asTask.MakeGenericMethod($resultType).Invoke($null, @($operation))
    $task.Wait() | Out-Null
    $task.Result
}

$engine = [Windows.Media.Ocr.OcrEngine]::TryCreateFromLanguage([Windows.Globalization.Language]::new('en-US'))
if (-not $engine) { $engine = [Windows.Media.Ocr.OcrEngine]::TryCreateFromUserProfileLanguages() }
if (-not $engine) { exit 3 }

for ($i = 0; $i -lt $Paths.Count; $i++) {
    $stream = $null
    try {
        $file = Await ([Windows.Storage.StorageFile]::GetFileFromPathAsync($Paths[$i])) ([Windows.Storage.StorageFile])
        $stream = Await ($file.OpenAsync([Windows.Storage.FileAccessMode]::Read)) ([Windows.Storage.Streams.IRandomAccessStream])
        $decoder = Await ([Windows.Graphics.Imaging.BitmapDecoder]::CreateAsync($stream)) ([Windows.Graphics.Imaging.BitmapDecoder])
        # OCR wants 8-bit BGRA; greyscale or RGB PNGs are converted on decode
        $bitmap = Await ($decoder.GetSoftwareBitmapAsync(
            [Windows.Graphics.Imaging.BitmapPixelFormat]::Bgra8,
            [Windows.Graphics.Imaging.BitmapAlphaMode]::Premultiplied)) ([Windows.Graphics.Imaging.SoftwareBitmap])
        $result = Await ($engine.RecognizeAsync($bitmap)) ([Windows.Media.Ocr.OcrResult])
        foreach ($line in $result.Lines) {
            $x1 = [double]::MaxValue; $y1 = [double]::MaxValue; $x2 = 0; $y2 = 0
            foreach ($word in $line.Words) {
                $r = $word.BoundingRect
                $x1 = [Math]::Min($x1, $r.X); $y1 = [Math]::Min($y1, $r.Y)
                $x2 = [Math]::Max($x2, $r.X + $r.Width); $y2 = [Math]::Max($y2, $r.Y + $r.Height)
            }
            $cx = [int](($x1 + $x2) / 2); $cy = [int](($y1 + $y2) / 2)
            [Console]::Out.WriteLine("$i`t$cx`t$cy`t$($line.Text)")
        }
    } catch {
        [Console]::Error.WriteLine("ocr failed for image ${i}: $($_.Exception.Message)")
    } finally {
        if ($stream) { $stream.Dispose() }
    }
}
