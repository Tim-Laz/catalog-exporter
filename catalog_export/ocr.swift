// Prints every text line macOS Vision finds in each image:  <file>\t<x>\t<y>\t<text>
// (x, y = centre of the line in pixels, origin top-left). Used to read the apartment
// type printed on floor plans.
import AppKit
import Foundation
import Vision

for path in CommandLine.arguments.dropFirst() {
    guard let img = NSImage(contentsOf: URL(fileURLWithPath: path)),
          let cg = img.cgImage(forProposedRect: nil, context: nil, hints: nil) else { continue }
    let req = VNRecognizeTextRequest()
    req.recognitionLevel = .accurate
    req.usesLanguageCorrection = false
    req.minimumTextHeight = 0.004
    try? VNImageRequestHandler(cgImage: cg).perform([req])
    let w = Double(cg.width), h = Double(cg.height)
    for o in req.results ?? [] {
        guard let t = o.topCandidates(1).first else { continue }
        let b = o.boundingBox
        print("\(path)\t\(Int(b.midX * w))\t\(Int((1 - b.midY) * h))\t\(t.string)")
    }
}
