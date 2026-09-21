import AppKit
let output = CommandLine.arguments[1]
let image = NSImage(size: NSSize(width: 1024, height: 1024))
image.lockFocus()
let background = NSBezierPath(roundedRect: NSRect(x: 64, y: 64, width: 896, height: 896), xRadius: 190, yRadius: 190)
NSColor(calibratedRed: 0.12, green: 0.32, blue: 0.24, alpha: 1).setFill(); background.fill()
let frame = NSBezierPath(roundedRect: NSRect(x: 225, y: 216, width: 575, height: 565), xRadius: 58, yRadius: 58)
NSColor(calibratedRed: 0.90, green: 0.95, blue: 0.87, alpha: 1).setFill(); frame.fill()
NSColor(calibratedRed: 0.23, green: 0.49, blue: 0.37, alpha: 1).setFill()
NSBezierPath(roundedRect: NSRect(x: 272, y: 330, width: 481, height: 403), xRadius: 25, yRadius: 25).fill()
NSColor(calibratedRed: 0.96, green: 0.78, blue: 0.43, alpha: 1).setFill()
NSBezierPath(ovalIn: NSRect(x: 559, y: 545, width: 119, height: 119)).fill()
NSColor(calibratedRed: 0.71, green: 0.85, blue: 0.69, alpha: 1).setFill()
let mountain = NSBezierPath(); mountain.move(to: NSPoint(x: 272, y: 330)); mountain.line(to: NSPoint(x: 444, y: 574)); mountain.line(to: NSPoint(x: 625, y: 330)); mountain.close(); mountain.fill()
NSColor(calibratedRed: 0.49, green: 0.68, blue: 0.52, alpha: 1).setFill()
let hill = NSBezierPath(); hill.move(to: NSPoint(x: 501, y: 330)); hill.line(to: NSPoint(x: 652, y: 512)); hill.line(to: NSPoint(x: 753, y: 390)); hill.line(to: NSPoint(x: 753, y: 330)); hill.close(); hill.fill()
image.unlockFocus()
let rep = NSBitmapImageRep(data: image.tiffRepresentation!)!
try rep.representation(using: .png, properties: [:])!.write(to: URL(fileURLWithPath: output))
