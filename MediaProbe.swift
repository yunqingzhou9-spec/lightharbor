import Foundation
import ImageIO
import Vision

func inspect(_ path: String, scene: Bool, thumbnail: Bool) -> [String: Any] {
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
        let options: [CFString: Any] = [kCGImageSourceCreateThumbnailFromImageAlways: true, kCGImageSourceThumbnailMaxPixelSize: 480, kCGImageSourceCreateThumbnailWithTransform: true]
        if let image = CGImageSourceCreateThumbnailAtIndex(src, 0, options as CFDictionary) {
            let data = NSMutableData()
            if let dest = CGImageDestinationCreateWithData(data as CFMutableData, "public.jpeg" as CFString, 1, nil) {
                CGImageDestinationAddImage(dest, image, nil)
                if CGImageDestinationFinalize(dest) { out["thumbnail"] = data.base64EncodedString() }
            }
        }
        if out["thumbnail"] == nil { out["warning"] = "此格式无法生成缩略图" }
    }
    if let date = exif[kCGImagePropertyExifDateTimeOriginal as String] as? String ?? tiff[kCGImagePropertyTIFFDateTime as String] as? String {
        out["date"] = date
    }
    if let gps = props[kCGImagePropertyGPSDictionary as String] as? [String: Any],
       var lat = gps[kCGImagePropertyGPSLatitude as String] as? Double,
       var lon = gps[kCGImagePropertyGPSLongitude as String] as? Double {
        if gps[kCGImagePropertyGPSLatitudeRef as String] as? String == "S" { lat = -lat }
        if gps[kCGImagePropertyGPSLongitudeRef as String] as? String == "W" { lon = -lon }
        if lat.isFinite && lon.isFinite && abs(lat) <= 90 && abs(lon) <= 180 { out["gps"] = [lat, lon] }
    }
    if scene {
        let opts: [CFString: Any] = [kCGImageSourceCreateThumbnailFromImageAlways: true, kCGImageSourceThumbnailMaxPixelSize: 1024, kCGImageSourceCreateThumbnailWithTransform: true]
        if let thumb = CGImageSourceCreateThumbnailAtIndex(src, 0, opts as CFDictionary) {
            do {
                let req = VNClassifyImageRequest()
                try VNImageRequestHandler(cgImage: thumb, options: [:]).perform([req])
                out["labels"] = (req.results ?? []).prefix(30).map { ["label": $0.identifier, "score": $0.confidence] as [String: Any] }
            } catch { out["warning"] = "景色识别不可用：\(error.localizedDescription)" }
        } else { out["warning"] = "系统无法生成识别缩略图" }
    }
    return out
}
while let line = readLine() {
    autoreleasepool {
        do {
            let obj = try JSONSerialization.jsonObject(with: Data(line.utf8)) as? [String: Any] ?? [:]
            let result = inspect(obj["path"] as? String ?? "", scene: obj["scene"] as? Bool ?? false, thumbnail: obj["thumbnail"] as? Bool ?? false)
            let data = try JSONSerialization.data(withJSONObject: result, options: [.sortedKeys])
            print(String(decoding: data, as: UTF8.self))
            fflush(stdout)
        } catch { print("{\"warning\":\"元数据读取失败\"}"); fflush(stdout) }
    }
}
