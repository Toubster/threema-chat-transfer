// SPDX-License-Identifier: AGPL-3.0-or-later
// Media.swift - thumbnails (ImageIO / AVFoundation), dimensions and durations on the Mac.
import AVFoundation
import CoreGraphics
import CryptoKit
import Foundation
import ImageIO
import UniformTypeIdentifiers

enum Media {
    /// Max thumbnail edge. iOS: ImageURLSenderItemCreator.imageThumbnailMaxSize = max(128, min(imageMaxSize/3, 512)).
    static let thumbMaxPixel = 512

    static func sha256Hex(of url: URL) -> String? {
        guard let h = FileHandle(forReadingAtPath: url.path) else { return nil }
        defer { try? h.close() }
        var hasher = SHA256()
        while true {
            let chunk = autoreleasepool { h.readData(ofLength: 8 << 20) }
            if chunk.isEmpty { break }
            hasher.update(data: chunk)
        }
        return hasher.finalize().map { String(format: "%02x", $0) }.joined()
    }

    static func deterministicBytes(_ label: String, _ uid: String, count: Int) -> Data {
        var out = Data()
        var i = 0
        while out.count < count {
            out.append(contentsOf: SHA256.hash(data: Data("threema-import/\(label)/\(uid)/\(i)".utf8)))
            i += 1
        }
        return out.prefix(count)
    }

    /// Pixel size as UIImage(data:).size would report it (EXIF orientation 5-8 swaps width/height).
    static func imageSize(data: Data) -> (Int, Int)? {
        guard let src = CGImageSourceCreateWithData(data as CFData, nil) else { return nil }
        return imageSize(source: src)
    }

    static func imageSize(url: URL) -> (Int, Int)? {
        guard let src = CGImageSourceCreateWithURL(url as CFURL, nil) else { return nil }
        return imageSize(source: src)
    }

    private static func imageSize(source src: CGImageSource) -> (Int, Int)? {
        guard CGImageSourceGetCount(src) > 0,
              let props = CGImageSourceCopyPropertiesAtIndex(src, 0, nil) as? [CFString: Any],
              let w = (props[kCGImagePropertyPixelWidth] as? NSNumber)?.intValue,
              let h = (props[kCGImagePropertyPixelHeight] as? NSNumber)?.intValue else { return nil }
        let o = (props[kCGImagePropertyOrientation] as? NSNumber)?.intValue ?? 1
        return o >= 5 && o <= 8 ? (h, w) : (w, h)
    }

    private static func encode(_ image: CGImage, png: Bool) -> Data? {
        let out = NSMutableData()
        let type = (png ? UTType.png : UTType.jpeg).identifier as CFString
        guard let dest = CGImageDestinationCreateWithData(out, type, 1, nil) else { return nil }
        let opts: [CFString: Any] = png ? [:] : [kCGImageDestinationLossyCompressionQuality: 0.8]
        CGImageDestinationAddImage(dest, image, opts as CFDictionary)
        guard CGImageDestinationFinalize(dest) else { return nil }
        return out as Data
    }

    /// Thumbnail for an image file (first frame for GIF), orientation applied, max 512 px.
    static func imageThumbnail(url: URL, keepAlpha: Bool) -> (data: Data, mime: String)? {
        guard let src = CGImageSourceCreateWithURL(url as CFURL, nil), CGImageSourceGetCount(src) > 0 else { return nil }
        let opts: [CFString: Any] = [
            kCGImageSourceCreateThumbnailFromImageAlways: true,
            kCGImageSourceCreateThumbnailWithTransform: true,
            kCGImageSourceThumbnailMaxPixelSize: thumbMaxPixel,
        ]
        guard let img = CGImageSourceCreateThumbnailAtIndex(src, 0, opts as CFDictionary) else { return nil }
        let alpha = img.alphaInfo
        let hasAlpha = keepAlpha && !(alpha == .none || alpha == .noneSkipFirst || alpha == .noneSkipLast)
        guard let d = encode(img, png: hasAlpha) else { return nil }
        return (d, hasAlpha ? "image/png" : "image/jpeg")
    }

    /// AVFoundation needs a file extension; media files are stored as <uid> without one -> temp symlink.
    static func withExtension<T>(_ url: URL, mime: String, tmpDir: URL, _ body: (URL) -> T?) -> T? {
        let ext: String
        switch mime.lowercased() {
        case "audio/aac", "audio/mp4", "audio/x-m4a", "audio/m4a": ext = "m4a"   // iOS: "Workaround for audio messages from Android"
        case "audio/mpeg", "audio/mp3": ext = "mp3"
        case "audio/ogg", "audio/opus": ext = "ogg"
        case "video/quicktime": ext = "mov"
        case "video/mpeg": ext = "mpg"
        case "video/3gpp": ext = "3gp"
        default:
            ext = UTType(mimeType: mime)?.preferredFilenameExtension ?? (mime.hasPrefix("video/") ? "mp4" : "m4a")
        }
        let link = tmpDir.appendingPathComponent(url.lastPathComponent + "." + ext)
        try? FileManager.default.removeItem(at: link)
        do { try FileManager.default.createSymbolicLink(at: link, withDestinationURL: url) } catch { return nil }
        defer { try? FileManager.default.removeItem(at: link) }
        return body(link)
    }

    static func duration(url: URL, mime: String, tmpDir: URL) -> Double? {
        withExtension(url, mime: mime, tmpDir: tmpDir) { link in
            let asset = AVURLAsset(url: link)
            let s = CMTimeGetSeconds(asset.duration)
            return s.isFinite && s > 0 ? s : nil
        }
    }

    static func videoThumbnail(url: URL, mime: String, tmpDir: URL) -> (data: Data, mime: String)? {
        withExtension(url, mime: mime, tmpDir: tmpDir) { link in
            let asset = AVURLAsset(url: link)
            let gen = AVAssetImageGenerator(asset: asset)
            gen.appliesPreferredTrackTransform = true
            gen.maximumSize = CGSize(width: thumbMaxPixel, height: thumbMaxPixel)
            var actual = CMTime.zero
            let img: CGImage? = (try? gen.copyCGImage(at: .zero, actualTime: &actual))
                ?? (try? gen.copyCGImage(at: CMTime(seconds: 0.5, preferredTimescale: 600), actualTime: &actual))
            guard let img, let d = encode(img, png: false) else { return nil }
            return (d, "image/jpeg")
        }
    }

    static func videoSize(url: URL, mime: String, tmpDir: URL) -> (Int, Int)? {
        withExtension(url, mime: mime, tmpDir: tmpDir) { link in
            let asset = AVURLAsset(url: link)
            guard let track = asset.tracks(withMediaType: .video).first else { return nil }
            let s = track.naturalSize.applying(track.preferredTransform)
            let w = Int(abs(s.width).rounded()), h = Int(abs(s.height).rounded())
            return w > 0 && h > 0 ? (w, h) : nil
        }
    }
}
