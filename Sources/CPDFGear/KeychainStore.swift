import Foundation
import Security

/// Lưu API key vào Keychain của macOS thay vì UserDefaults (plist đọc được
/// dưới dạng văn bản thuần nếu ai đó có quyền truy cập máy). App không
/// sandbox nên không cần entitlement `keychain-access-groups` gì thêm.
enum KeychainStore {
    private static let service = "dev.cuonghoang.cpdfgear"

    /// Đọc giá trị đã lưu; nếu chưa có trong Keychain nhưng còn sót lại ở
    /// UserDefaults (bản cũ trước khi chuyển sang Keychain dùng `@AppStorage`
    /// cho cùng key này), di trú 1 lần: copy sang Keychain rồi xoá khỏi
    /// UserDefaults, để không còn lưu trùng ở 2 nơi.
    static func load(_ key: String) -> String {
        if let value = readKeychain(key), !value.isEmpty {
            return value
        }
        let legacy = UserDefaults.standard.string(forKey: key) ?? ""
        if !legacy.isEmpty {
            save(key, value: legacy)
            UserDefaults.standard.removeObject(forKey: key)
        }
        return legacy
    }

    static func save(_ key: String, value: String) {
        let query: [String: Any] = [
            kSecClass as String: kSecClassGenericPassword,
            kSecAttrService as String: service,
            kSecAttrAccount as String: key,
        ]
        if value.isEmpty {
            SecItemDelete(query as CFDictionary)
            return
        }
        let data = Data(value.utf8)
        var addQuery = query
        addQuery[kSecValueData as String] = data
        let status = SecItemAdd(addQuery as CFDictionary, nil)
        if status == errSecDuplicateItem {
            SecItemUpdate(query as CFDictionary, [kSecValueData as String: data] as CFDictionary)
        }
    }

    private static func readKeychain(_ key: String) -> String? {
        let query: [String: Any] = [
            kSecClass as String: kSecClassGenericPassword,
            kSecAttrService as String: service,
            kSecAttrAccount as String: key,
            kSecReturnData as String: true,
            kSecMatchLimit as String: kSecMatchLimitOne,
        ]
        var result: AnyObject?
        guard SecItemCopyMatching(query as CFDictionary, &result) == errSecSuccess,
              let data = result as? Data else {
            return nil
        }
        return String(data: data, encoding: .utf8)
    }
}
