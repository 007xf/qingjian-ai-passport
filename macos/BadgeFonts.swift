import Foundation
import CoreText

enum BadgeFonts {
    static func register() {
        for filename in ["QingjianNotoSans-Regular", "Montserrat-Medium"] {
            if let url = Bundle.main.url(forResource: filename, withExtension: "ttf", subdirectory: "Fonts") {
                CTFontManagerRegisterFontsForURL(url as CFURL, .process, nil)
            }
        }
    }
}
