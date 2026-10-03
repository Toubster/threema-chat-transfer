// SPDX-License-Identifier: AGPL-3.0-or-later
import Foundation
import Security

/// Generated iPhone backup password (DESIGN §9): 6 groups of 4 characters, no easily confused characters
/// (no 0/O, 1/I/L), upper case and digits only so it is easy to write down: 31 symbols, 24 characters ≈ 119 bits.
public enum PasswordGenerator {
    public static let alphabet = Array("ABCDEFGHJKMNPQRSTUVWXYZ23456789")
    public static let groups = 6
    public static let groupLength = 4
    /// A password the user chooses must have at least this many characters.
    public static let minimumOwnLength = 10

    public static func generate() -> String {
        var out: [String] = []
        for _ in 0..<groups {
            var g = ""
            for _ in 0..<groupLength { g.append(alphabet[uniformIndex(alphabet.count)]) }
            out.append(g)
        }
        return out.joined(separator: "-")
    }

    /// Unbiased random index from the system CSPRNG.
    static func uniformIndex(_ upperBound: Int) -> Int {
        let limit = UInt32.max - (UInt32.max % UInt32(upperBound))
        while true {
            var r: UInt32 = 0
            let status = withUnsafeMutableBytes(of: &r) { SecRandomCopyBytes(kSecRandomDefault, 4, $0.baseAddress!) }
            precondition(status == errSecSuccess, "SecRandomCopyBytes failed")
            if r < limit { return Int(r % UInt32(upperBound)) }
        }
    }

    public static func isAcceptableOwn(_ s: String) -> Bool { s.count >= minimumOwnLength }

    /// The S10a check "enter the last 4 characters" (case-insensitive, separators ignored).
    public static func matchesLastFour(_ input: String, of password: String) -> Bool {
        let clean = { (s: String) in s.uppercased().filter { $0 != "-" && $0 != " " } }
        let tail = String(clean(password).suffix(4))
        return tail.count == 4 && clean(input) == tail
    }
}
