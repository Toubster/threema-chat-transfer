// SPDX-License-Identifier: AGPL-3.0-or-later
// render_dmg_background - draws the DMG window background (DESIGN §14.1 step 7, §14.3 four Gatekeeper steps,
// §11.3 disclaimer) from packaging/dmg/steps.json and layout.json. Owner: pack.
//
//   render_dmg_background <steps.json> <layout.json> <AppName> <out.png> <scale 1|2>
//
// Pure drawing (no photos, no screenshots). The PNG gets a tEXt chunk "tm-demo=1" (generated image marker of the
// scrub check, DESIGN §10.3) so the scan accepts it if it is ever committed or scanned from the DMG.
import CoreGraphics
import CoreText
import Foundation
import ImageIO
import UniformTypeIdentifiers

let a = CommandLine.arguments
guard a.count == 6, let scale = Double(a[5]),
      let steps = try? JSONSerialization.jsonObject(with: Data(contentsOf: URL(fileURLWithPath: a[1]))) as? [String: Any],
      let layout = try? JSONSerialization.jsonObject(with: Data(contentsOf: URL(fileURLWithPath: a[2]))) as? [String: Any]
else {
    FileHandle.standardError.write("usage: render_dmg_background <steps.json> <layout.json> <App> <out.png> <scale>\n".data(using: .utf8)!)
    exit(2)
}
let app = a[3]
func t(_ any: Any?, _ lang: String) -> String { ((any as? [String: String])?[lang] ?? "").replacingOccurrences(of: "{App}", with: app) }
let win = layout["window"] as! [String: Double]
let W = win["width"]!, H = win["height"]!
let pos = layout["positions"] as! [String: [Double]]
let icon = layout["icon_size"] as! Double

let cs = CGColorSpace(name: CGColorSpace.sRGB)!
guard let ctx = CGContext(data: nil, width: Int(W * scale), height: Int(H * scale), bitsPerComponent: 8, bytesPerRow: 0,
                          space: cs, bitmapInfo: CGImageAlphaInfo.premultipliedLast.rawValue) else { exit(1) }
ctx.scaleBy(x: scale, y: scale)
// top-left origin like Finder's icon positions
ctx.translateBy(x: 0, y: H); ctx.scaleBy(x: 1, y: -1)

func rgb(_ r: Double, _ g: Double, _ b: Double, _ al: Double = 1) -> CGColor { CGColor(colorSpace: cs, components: [r, g, b, al])! }
let ink = rgb(0.11, 0.12, 0.14), soft = rgb(0.38, 0.40, 0.44), accent = rgb(0.10, 0.42, 0.72)
ctx.setFillColor(rgb(0.965, 0.968, 0.975)); ctx.fill(CGRect(x: 0, y: 0, width: W, height: H))

/// CTParagraphStyle from (specifier, value) pairs; values live in heap memory for the duration of the call
func makeStyle(_ specs: [(CTParagraphStyleSpecifier, CGFloat)], align: CTTextAlignment? = nil) -> CTParagraphStyle {
    let vals = UnsafeMutablePointer<CGFloat>.allocate(capacity: max(specs.count, 1))
    let al = UnsafeMutablePointer<CTTextAlignment>.allocate(capacity: 1)
    defer { vals.deallocate(); al.deallocate() }
    var settings: [CTParagraphStyleSetting] = []
    for (i, (spec, v)) in specs.enumerated() {
        vals[i] = v
        settings.append(CTParagraphStyleSetting(spec: spec, valueSize: MemoryLayout<CGFloat>.size, value: vals + i))
    }
    if let align {
        al.pointee = align
        settings.append(CTParagraphStyleSetting(spec: .alignment, valueSize: MemoryLayout<CTTextAlignment>.size, value: al))
    }
    return CTParagraphStyleCreate(settings, settings.count)
}

func font(_ size: CGFloat, bold: Bool = false) -> CTFont {
    let f = CTFontCreateUIFontForLanguage(.system, size, nil)!
    return bold ? (CTFontCreateCopyWithSymbolicTraits(f, 0, nil, .traitBold, .traitBold) ?? f) : f
}
/// draws text with its top-left at (x, y) (top-left coordinates), wrapped to width; returns the used height
@discardableResult
func text(_ s: String, x: Double, y: Double, width: Double, size: CGFloat, bold: Bool = false, color: CGColor,
          center: Bool = false) -> Double {
    let style = makeStyle([], align: center ? .center : .left)
    let attr = NSAttributedString(string: s, attributes: [
        NSAttributedString.Key(kCTFontAttributeName as String): font(size, bold: bold),
        NSAttributedString.Key(kCTForegroundColorAttributeName as String): color,
        NSAttributedString.Key(kCTParagraphStyleAttributeName as String): style])
    let fs = CTFramesetterCreateWithAttributedString(attr)
    let need = CTFramesetterSuggestFrameSizeWithConstraints(fs, CFRange(location: 0, length: 0), nil,
                                                             CGSize(width: width, height: 10_000), nil)
    ctx.saveGState()
    // CoreText draws bottom-up: flip locally
    ctx.translateBy(x: x, y: y + need.height); ctx.scaleBy(x: 1, y: -1)
    let frame = CTFramesetterCreateFrame(fs, CFRange(location: 0, length: 0),
                                         CGPath(rect: CGRect(x: 0, y: 0, width: width, height: need.height), transform: nil), nil)
    CTFrameDraw(frame, ctx)
    ctx.restoreGState()
    return need.height
}

// title: German line, English line (the product name is long; one line each never wraps mid-name)
text(t(steps["title"], "de") + "\n" + t(steps["title"], "en"), x: 40, y: 18, width: W - 80, size: 17, bold: true,
     color: ink, center: true)
// arrow between the app and the Applications alias
let appP = pos["app"]!, appsP = pos["Applications"]!
let y0 = appP[1], x1 = appP[0] + icon / 2 + 28, x2 = appsP[0] - icon / 2 - 28
ctx.setStrokeColor(accent); ctx.setLineWidth(3); ctx.setLineCap(.round)
ctx.move(to: CGPoint(x: x1, y: y0)); ctx.addLine(to: CGPoint(x: x2, y: y0)); ctx.strokePath()
ctx.setFillColor(accent)
ctx.move(to: CGPoint(x: x2 + 10, y: y0)); ctx.addLine(to: CGPoint(x: x2 - 6, y: y0 - 9)); ctx.addLine(to: CGPoint(x: x2 - 6, y: y0 + 9))
ctx.closePath(); ctx.fillPath()
text(t(steps["arrow_hint"], "de") + "\n" + t(steps["arrow_hint"], "en"), x: x1, y: y0 + 12, width: x2 - x1, size: 11,
     color: soft, center: true)

// the four steps (DE bold, EN below)
var y = 248.0
let list = steps["steps"] as! [[String: String]]
let box = CGRect(x: 36, y: y - 14, width: W - 72, height: Double(list.count) * 38 + 18)
ctx.setFillColor(rgb(1, 1, 1)); ctx.addPath(CGPath(roundedRect: box, cornerWidth: 12, cornerHeight: 12, transform: nil)); ctx.fillPath()
for (i, s) in list.enumerated() {
    ctx.setFillColor(accent)
    ctx.fillEllipse(in: CGRect(x: 54, y: y + 1, width: 24, height: 24))
    text("\(i + 1)", x: 54, y: y + 5, width: 24, size: 13, bold: true, color: rgb(1, 1, 1), center: true)
    text(t(s, "de"), x: 92, y: y, width: W - 150, size: 13, bold: true, color: ink)
    text(t(s, "en"), x: 92, y: y + 17, width: W - 150, size: 11.5, color: soft)
    y += 38
}
text(t(steps["readme_hint"], "de") + "  ·  " + t(steps["readme_hint"], "en"), x: 40, y: box.maxY + 10, width: W - 80,
     size: 11.5, color: soft, center: true)
// disclaimer (DESIGN §11.3, short form)
text(t(steps["disclaimer"], "de") + "\n" + t(steps["disclaimer"], "en"), x: 30, y: H - 44, width: W - 60, size: 8.5,
     color: soft, center: true)

guard let img = ctx.makeImage() else { exit(1) }
let data = NSMutableData()
let dest = CGImageDestinationCreateWithData(data, UTType.png.identifier as CFString, 1, nil)!
CGImageDestinationAddImage(dest, img, [kCGImagePropertyDPIWidth: 72 * scale, kCGImagePropertyDPIHeight: 72 * scale] as CFDictionary)
guard CGImageDestinationFinalize(dest) else { exit(1) }

// insert tEXt "Comment\0tm-demo=1 ..." before IEND
func crc32(_ bytes: [UInt8]) -> UInt32 {
    var c: UInt32 = 0xFFFF_FFFF
    for b in bytes {
        c ^= UInt32(b)
        for _ in 0..<8 { c = (c & 1) != 0 ? (0xEDB8_8320 ^ (c >> 1)) : (c >> 1) }
    }
    return c ^ 0xFFFF_FFFF
}
var png = [UInt8](data as Data)
let payload = [UInt8]("tEXt".utf8) + [UInt8]("Comment".utf8) + [0] + [UInt8]("tm-demo=1 generated by render_dmg_background".utf8)
var chunk: [UInt8] = []
let len = UInt32(payload.count - 4)
chunk += [UInt8(len >> 24), UInt8((len >> 16) & 0xFF), UInt8((len >> 8) & 0xFF), UInt8(len & 0xFF)]
chunk += payload
let crc = crc32(payload)
chunk += [UInt8(crc >> 24), UInt8((crc >> 16) & 0xFF), UInt8((crc >> 8) & 0xFF), UInt8(crc & 0xFF)]
png.insert(contentsOf: chunk, at: png.count - 12)   // IEND chunk is the last 12 bytes
try! Data(png).write(to: URL(fileURLWithPath: a[4]))
print("render_dmg_background: \(Int(W * scale))x\(Int(H * scale))")
