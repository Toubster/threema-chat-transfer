// SPDX-License-Identifier: AGPL-3.0-or-later
// render_readme - typesets packaging/readme-first/<lang>.md (a small Markdown subset) into an A4 PDF with CoreText.
// Used by packaging/make-dmg.sh for "Zuerst lesen.pdf" / "Read me first.pdf" (DESIGN §14.1 step 7). Owner: pack.
//
//   render_readme <in.md> <out.pdf> <pdf-title>
//
// Subset: "# " title, "## " heading, "1. " numbered step, "- " bullet, blank line = paragraph break,
// **bold** inline, <!-- comments --> dropped. Placeholders ({App}, ...) are replaced by the caller beforehand.
// The PDF carries a title and a generic creator only (no author, no user or machine names).
import CoreGraphics
import CoreText
import Foundation

let args = CommandLine.arguments
guard args.count == 4, let src = try? String(contentsOfFile: args[1], encoding: .utf8) else {
    FileHandle.standardError.write("usage: render_readme <in.md> <out.pdf> <title>\n".data(using: .utf8)!)
    exit(2)
}

let page = CGRect(x: 0, y: 0, width: 595.28, height: 841.89)      // A4
let margin: CGFloat = 56
let body = CTFontCreateUIFontForLanguage(.system, 11, nil)!
func bold(_ f: CTFont) -> CTFont { CTFontCreateCopyWithSymbolicTraits(f, 0, nil, .traitBold, .traitBold) ?? f }
let h1 = bold(CTFontCreateUIFontForLanguage(.system, 21, nil)!)
let h2 = bold(CTFontCreateUIFontForLanguage(.system, 14, nil)!)
let gray = CGColor(gray: 0.35, alpha: 1)

/// CTParagraphStyle from (specifier, value) pairs; values live in heap memory for the duration of the call
func makeStyle(_ specs: [(CTParagraphStyleSpecifier, CGFloat)], align: CTTextAlignment? = nil,
               tab: CGFloat? = nil) -> CTParagraphStyle {
    let vals = UnsafeMutablePointer<CGFloat>.allocate(capacity: max(specs.count, 1))
    let al = UnsafeMutablePointer<CTTextAlignment>.allocate(capacity: 1)
    let tabs = UnsafeMutablePointer<CFArray>.allocate(capacity: 1)
    defer { vals.deallocate(); al.deallocate(); tabs.deallocate() }
    var settings: [CTParagraphStyleSetting] = []
    if let tab {
        tabs.initialize(to: [CTTextTabCreate(.left, Double(tab), nil)] as CFArray)
        settings.append(CTParagraphStyleSetting(spec: .tabStops, valueSize: MemoryLayout<CFArray>.size, value: tabs))
    }
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

func para(indent: CGFloat = 0, first: CGFloat = 0, before: CGFloat = 0, after: CGFloat = 6) -> CTParagraphStyle {
    makeStyle([(.headIndent, indent), (.firstLineHeadIndent, first), (.paragraphSpacingBefore, before),
               (.paragraphSpacing, after), (.lineSpacingAdjustment, 2)], tab: indent > 0 ? indent : nil)
}

let out = NSMutableAttributedString()
func append(_ text: String, font: CTFont, style: CTParagraphStyle, color: CGColor? = nil) {
    // inline **bold**
    let parts = text.components(separatedBy: "**")
    for (i, part) in parts.enumerated() where !part.isEmpty {
        var attrs: [NSAttributedString.Key: Any] = [
            NSAttributedString.Key(kCTFontAttributeName as String): i % 2 == 1 ? bold(font) : font,
            NSAttributedString.Key(kCTParagraphStyleAttributeName as String): style]
        if let color { attrs[NSAttributedString.Key(kCTForegroundColorAttributeName as String)] = color }
        out.append(NSAttributedString(string: part, attributes: attrs))
    }
    out.append(NSAttributedString(string: "\n", attributes: [
        NSAttributedString.Key(kCTFontAttributeName as String): font,
        NSAttributedString.Key(kCTParagraphStyleAttributeName as String): style]))
}

var noComments = src
while let r = noComments.range(of: "<!--"), let e = noComments.range(of: "-->", range: r.upperBound..<noComments.endIndex) {
    noComments.removeSubrange(r.lowerBound..<e.upperBound)
}
var buffer: [String] = []
var kind = "p"
func flush() {
    guard !buffer.isEmpty else { return }
    let text = buffer.joined(separator: " ")
    buffer = []
    switch kind {
    case "h1": append(text, font: h1, style: para(after: 4))
    case "sub": append(text, font: body, style: para(after: 10), color: gray)
    case "h2": append(text, font: h2, style: para(before: 10, after: 5))
    case "li":
        append(text, font: body, style: para(indent: 20, first: 0, after: 4))
    default: append(text, font: body, style: para(after: 7))
    }
}
var sawTitle = false
for raw in noComments.components(separatedBy: "\n") {
    let line = raw.trimmingCharacters(in: .whitespaces)
    if line.isEmpty { flush(); kind = "p"; continue }
    if line.hasPrefix("# ") { flush(); kind = "h1"; buffer = [String(line.dropFirst(2))]; flush(); sawTitle = true; kind = "sub"; continue }
    if line.hasPrefix("## ") { flush(); kind = "h2"; buffer = [String(line.dropFirst(3))]; flush(); kind = "p"; continue }
    if let m = line.range(of: #"^\d+\. "#, options: .regularExpression) {
        flush(); kind = "li"; buffer = [String(line[m].dropLast()) + "\t" + line[m.upperBound...]]; continue
    }
    if line.hasPrefix("- ") { flush(); kind = "li"; buffer = ["•\t" + line.dropFirst(2)]; continue }
    if kind == "sub" && !sawTitle { kind = "p" }
    buffer.append(line)
}
flush()

var mediaBox = page
let info: [CFString: Any] = [kCGPDFContextTitle: args[3], kCGPDFContextCreator: "Chat Transfer for Threema packaging (render_readme)"]
guard let ctx = CGContext(URL(fileURLWithPath: args[2]) as CFURL, mediaBox: &mediaBox, info as CFDictionary) else {
    FileHandle.standardError.write("cannot create PDF\n".data(using: .utf8)!)
    exit(1)
}
let framesetter = CTFramesetterCreateWithAttributedString(out)
var start = 0
var pages = 0
while start < out.length {
    ctx.beginPDFPage(nil)
    let path = CGPath(rect: page.insetBy(dx: margin, dy: margin), transform: nil)
    let frame = CTFramesetterCreateFrame(framesetter, CFRange(location: start, length: 0), path, nil)
    CTFrameDraw(frame, ctx)
    let visible = CTFrameGetVisibleStringRange(frame)
    ctx.endPDFPage()
    pages += 1
    if visible.length == 0 { break }
    start += visible.length
}
ctx.closePDF()
print("render_readme: \(pages) page(s)")
