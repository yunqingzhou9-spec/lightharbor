import Foundation
import ImageIO
import CoreImage
import UniformTypeIdentifiers

func jpegPreview(_ image: CGImage, quality: String, source: String, warning: String? = nil) -> [String: Any] {
    let data = NSMutableData()
    guard let destination = CGImageDestinationCreateWithData(data, "public.jpeg" as CFString, 1, nil) else {
        return ["warning": "预览编码未成功"]
    }
    // Both ImageIO and Core Image have already applied direction to the pixels.
    // Do not copy EXIF orientation into the exported JPEG and rotate it again.
    CGImageDestinationAddImage(destination, image, [kCGImageDestinationLossyCompressionQuality: quality == "high" ? 0.9 : 0.75] as CFDictionary)
    guard CGImageDestinationFinalize(destination) else { return ["warning": "预览编码未成功"] }
    var result: [String: Any] = ["thumbnail": data.base64EncodedString(), "width": image.width, "height": image.height, "source": source]
    if let warning = warning { result["warning"] = warning }
    return result
}

func decodedRAW(_ url: URL, sourceType: CFString?, properties: [String: Any], maximum: Int) -> CGImage? {
    // CIRAWFilter can also accept some rendered images. Confirm the actual file
    // type before describing its output as sensor-data RAW decoding.
    guard let sourceType = sourceType,
          UTType(sourceType as String)?.conforms(to: .rawImage) == true else { return nil }
    guard let filter = CIRAWFilter(imageURL: url) else { return nil }
    let originalOrientation = filter.orientation
    let orientationNumber = (properties[kCGImagePropertyOrientation as String] as? NSNumber)?.uint32Value
    let orientation = orientationNumber.flatMap { CGImagePropertyOrientation(rawValue: $0) } ?? originalOrientation
    let native = filter.nativeSize
    guard native.width > 0, native.height > 0, native.width.isFinite, native.height.isFinite else { return nil }
    // Decode through the native RAW filter. Scale only down, never up. Normalize
    // all eight EXIF transforms once after disabling the filter's auto rotation.
    filter.orientation = .up
    filter.scaleFactor = Float(min(1, CGFloat(maximum) / max(native.width, native.height)))
    guard let output = filter.outputImage else { return nil }
    let oriented = output.oriented(forExifOrientation: Int32(orientation.rawValue))
    guard !oriented.extent.isEmpty, !oriented.extent.isInfinite else { return nil }
    let context = CIContext(options: [.cacheIntermediates: false])
    return context.createCGImage(oriented, from: oriented.extent, format: .RGBA8,
                                 colorSpace: CGColorSpace(name: CGColorSpace.sRGB)!)
}

func inspect(_ path: String, scene: Bool, thumbnail: Bool, quality: String = "low", raw: Bool = false) -> [String: Any] {
    var out: [String: Any] = [:]
    guard let src = CGImageSourceCreateWithURL(URL(fileURLWithPath: path) as CFURL, nil),
          let props = CGImageSourceCopyPropertiesAtIndex(src, 0, nil) as? [String: Any] else {
        return ["warning": "系统无法读取图片元数据"]
    }
    let exif = props[kCGImagePropertyExifDictionary as String] as? [String: Any] ?? [:]
    let tiff = props[kCGImagePropertyTIFFDictionary as String] as? [String: Any] ?? [:]
    out["camera"] = [tiff["Make"], tiff["Model"], exif["BodySerialNumber"]].compactMap { $0 as? String }.joined(separator: " / ")
    out["reliable_time"] = exif[kCGImagePropertyExifDateTimeOriginal as String] != nil
    out["subsecond"] = exif["SubsecTimeOriginal"] as? String ?? ""
    if thumbnail {
        guard ["low", "high"].contains(quality) else { return ["warning": "预览清晰度无效"] }
        let maximum = quality == "high" ? 3200 : 480
        let options: [CFString: Any] = [kCGImageSourceCreateThumbnailFromImageAlways: !raw, kCGImageSourceCreateThumbnailFromImageIfAbsent: !raw, kCGImageSourceThumbnailMaxPixelSize: maximum, kCGImageSourceCreateThumbnailWithTransform: true, kCGImageSourceShouldCache: false]
        let embedded = CGImageSourceCreateThumbnailAtIndex(src, 0, options as CFDictionary)
        var selected = embedded
        var source = raw ? "RAW 内嵌预览" : "照片预览"
        var warning: String? = nil
        if raw && (embedded == nil || quality == "high" && max(embedded!.width, embedded!.height) < maximum) {
            if let decoded = decodedRAW(URL(fileURLWithPath: path), sourceType: CGImageSourceGetType(src), properties: props, maximum: maximum) {
                if embedded == nil || decoded.width * decoded.height >= embedded!.width * embedded!.height {
                    selected = decoded; source = "RAW 解码预览"
                } else {
                    warning = "已尝试 RAW 解码，保留尺寸更大的内嵌预览（未放大）"
                }
            } else {
                warning = embedded == nil ? "系统暂时无法解码此 RAW，仍可复制原文件" : "系统暂时无法解码此 RAW；保留现有预览，未放大"
            }
        }
        if let image = selected {
            out.merge(jpegPreview(image, quality: quality, source: source, warning: warning)) { _, new in new }
        } else { out["warning"] = warning ?? "此格式无法生成缩略图，仍可复制原文件" }
    }
    if let date = exif[kCGImagePropertyExifDateTimeOriginal as String] as? String ?? tiff[kCGImagePropertyTIFFDateTime as String] as? String {
        out["date"] = date
    }
    return out
}
while let line = readLine() {
    autoreleasepool {
        do {
            let obj = try JSONSerialization.jsonObject(with: Data(line.utf8)) as? [String: Any] ?? [:]
            let result = inspect(obj["path"] as? String ?? "", scene: obj["scene"] as? Bool ?? false, thumbnail: obj["thumbnail"] as? Bool ?? false, quality: obj["quality"] as? String ?? "low", raw: obj["raw"] as? Bool ?? false)
            let data = try JSONSerialization.data(withJSONObject: result, options: [.sortedKeys])
            print(String(decoding: data, as: UTF8.self))
            fflush(stdout)
        } catch { print("{\"warning\":\"元数据读取失败\"}"); fflush(stdout) }
    }
}
