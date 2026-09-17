#import <Foundation/Foundation.h>

NS_ASSUME_NONNULL_BEGIN

// Parse a recall event without consulting account data or message databases.
// The returned values are session, canonical decimal messageID, and text.
FOUNDATION_EXPORT NSDictionary<NSString *, NSString *> * _Nullable
WCTParseRecallNotice(NSData *xml, BOOL chinese);

NS_ASSUME_NONNULL_END
