# Prints every text line the built-in Windows OCR finds in each image:
#   <file>`t<x>`t<y>`t<text>     (x, y = centre of the line in pixels, origin top-left)
# Used to read the apartment type printed on floor plans. `-Probe` only checks that an
# OCR engine is available (exit 0) and prints nothing.
# Runs under Windows PowerShell 5.1 (powershell.exe), which can load WinRT types.
param(
    [switch]$Probe,
    [Parameter(ValueFromRemainingArguments = $true)][string[]]$Paths
)
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8

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
if ($Probe) { exit 0 }

foreach ($path in $Paths) {
    $file = Await ([Windows.Storage.StorageFile]::GetFileFromPathAsync($path)) ([Windows.Storage.StorageFile])
    $stream = Await ($file.OpenAsync([Windows.Storage.FileAccessMode]::Read)) ([Windows.Storage.Streams.IRandomAccessStream])
    try {
        $decoder = Await ([Windows.Graphics.Imaging.BitmapDecoder]::CreateAsync($stream)) ([Windows.Graphics.Imaging.BitmapDecoder])
        $bitmap = Await ($decoder.GetSoftwareBitmapAsync()) ([Windows.Graphics.Imaging.SoftwareBitmap])
        $result = Await ($engine.RecognizeAsync($bitmap)) ([Windows.Media.Ocr.OcrResult])
        foreach ($line in $result.Lines) {
            $x1 = [double]::MaxValue; $y1 = [double]::MaxValue; $x2 = 0; $y2 = 0
            foreach ($word in $line.Words) {
                $r = $word.BoundingRect
                $x1 = [Math]::Min($x1, $r.X); $y1 = [Math]::Min($y1, $r.Y)
                $x2 = [Math]::Max($x2, $r.X + $r.Width); $y2 = [Math]::Max($y2, $r.Y + $r.Height)
            }
            $cx = [int](($x1 + $x2) / 2); $cy = [int](($y1 + $y2) / 2)
            [Console]::Out.WriteLine("$path`t$cx`t$cy`t$($line.Text)")
        }
    } finally {
        $stream.Dispose()
    }
}
