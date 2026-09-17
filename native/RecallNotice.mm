#import "RecallNotice.h"

#include <stdint.h>

namespace {
constexpr NSUInteger kMaximumXMLBytes = 64 * 1024;
constexpr NSUInteger kMaximumNoticeLength = 2048;
constexpr NSUInteger kMaximumSessionLength = 256;

bool IsTargetField(NSString *name) {
    return [name isEqualToString:@"session"] || [name isEqualToString:@"replacemsg"] ||
           [name isEqualToString:@"newmsgid"] || [name isEqualToString:@"newsvrid"] ||
           [name isEqualToString:@"msgid"];
}

bool ParsePositiveID(NSString *text, uint64_t *value) {
    if (text.length == 0 || text.length > 20) return false;
    uint64_t result = 0;
    for (NSUInteger index = 0; index < text.length; ++index) {
        const unichar character = [text characterAtIndex:index];
        if (character < '0' || character > '9') return false;
        const uint64_t digit = character - '0';
        if (result > (UINT64_MAX - digit) / 10) return false;
        result = result * 10 + digit;
    }
    if (result == 0) return false;
    *value = result;
    return true;
}

bool IsSenderPrefix(NSString *prefix) {
    // Prefixes are transport metadata, not a source for the displayed actor.
    if (![prefix hasSuffix:@"\n"]) return false;
    NSString *sender = [prefix substringToIndex:prefix.length - 1];
    if ([sender hasSuffix:@"\r"]) sender = [sender substringToIndex:sender.length - 1];
    if (![sender hasSuffix:@":"]) return false;
    sender = [sender substringToIndex:sender.length - 1];
    if (sender.length == 0 || sender.length > kMaximumSessionLength) return false;
    for (NSUInteger index = 0; index < sender.length; ++index) {
        const unichar character = [sender characterAtIndex:index];
        if (!((character >= 'a' && character <= 'z') ||
              (character >= 'A' && character <= 'Z') ||
              (character >= '0' && character <= '9') ||
              character == '_' || character == '-' || character == '.' || character == '@')) {
            return false;
        }
    }
    return true;
}
}  // namespace

@interface WCTRecallXMLParser : NSObject <NSXMLParserDelegate> {
    NSMutableArray<NSString *> *_elements;
    NSMutableDictionary<NSString *, NSString *> *_values;
    NSString *_fieldName;
    NSMutableString *_fieldText;
    NSUInteger _elementCount;
    BOOL _invalid;
    BOOL _sawRecall;
}
- (NSDictionary<NSString *, NSString *> *)noticeWithChinese:(BOOL)chinese;
@end

@implementation WCTRecallXMLParser

- (instancetype)init {
    self = [super init];
    if (self) {
        _elements = [NSMutableArray array];
        _values = [NSMutableDictionary dictionary];
    }
    return self;
}

- (void)reject:(NSXMLParser *)parser {
    _invalid = YES;
    [parser abortParsing];
}

- (void)parser:(NSXMLParser *)parser didStartElement:(NSString *)elementName
  namespaceURI:(NSString *)namespaceURI qualifiedName:(NSString *)qualifiedName
    attributes:(NSDictionary<NSString *, NSString *> *)attributes {
    (void)namespaceURI;
    (void)qualifiedName;
    if (_invalid) return;
    const NSUInteger depth = _elements.count + 1;
    if (++_elementCount > 256 || depth > 16 || _fieldName || [elementName containsString:@":"]) {
        [self reject:parser];
        return;
    }
    for (NSString *attribute in attributes) {
        if ([attribute isEqualToString:@"xmlns"] || [attribute containsString:@":"]) {
            [self reject:parser];
            return;
        }
    }
    if (depth == 1) {
        if (![elementName isEqualToString:@"sysmsg"] ||
            ![attributes[@"type"] isEqualToString:@"revokemsg"]) {
            [self reject:parser];
            return;
        }
    } else if (depth == 2) {
        if (![elementName isEqualToString:@"revokemsg"] || _sawRecall || attributes.count != 0) {
            [self reject:parser];
            return;
        }
        _sawRecall = YES;
    } else if ([elementName isEqualToString:@"sysmsg"] || [elementName isEqualToString:@"revokemsg"]) {
        [self reject:parser];
        return;
    }
    if (IsTargetField(elementName)) {
        if (depth != 3 || attributes.count != 0 || _values[elementName]) {
            [self reject:parser];
            return;
        }
        _fieldName = [elementName copy];
        _fieldText = [NSMutableString string];
    }
    [_elements addObject:elementName];
}

- (void)appendText:(NSString *)string parser:(NSXMLParser *)parser {
    if (_invalid) return;
    if (_fieldName) {
        const NSUInteger limit = [_fieldName isEqualToString:@"replacemsg"] ? kMaximumNoticeLength :
                                 ([_fieldName isEqualToString:@"session"] ? kMaximumSessionLength : 20);
        if (_fieldText.length > limit || string.length > limit - _fieldText.length) {
            [self reject:parser];
            return;
        }
        [_fieldText appendString:string];
    } else if (_elements.count <= 2 &&
               [string stringByTrimmingCharactersInSet:NSCharacterSet.whitespaceAndNewlineCharacterSet].length) {
        [self reject:parser];
    }
}

- (void)parser:(NSXMLParser *)parser foundCharacters:(NSString *)string {
    [self appendText:string parser:parser];
}

- (void)parser:(NSXMLParser *)parser foundIgnorableWhitespace:(NSString *)string {
    [self appendText:string parser:parser];
}

- (void)parser:(NSXMLParser *)parser foundCDATA:(NSData *)CDATABlock {
    NSString *text = [[NSString alloc] initWithData:CDATABlock encoding:NSUTF8StringEncoding];
    if (!text) [self reject:parser];
    else [self appendText:text parser:parser];
}

- (void)parser:(NSXMLParser *)parser didEndElement:(NSString *)elementName
  namespaceURI:(NSString *)namespaceURI qualifiedName:(NSString *)qualifiedName {
    (void)namespaceURI;
    (void)qualifiedName;
    if (_invalid) return;
    if (![_elements.lastObject isEqualToString:elementName]) {
        [self reject:parser];
        return;
    }
    if (_fieldName) {
        _values[_fieldName] = [_fieldText copy];
        _fieldName = nil;
        _fieldText = nil;
    }
    [_elements removeLastObject];
}

- (void)parser:(NSXMLParser *)parser foundProcessingInstructionWithTarget:(NSString *)target data:(NSString *)data {
    (void)target;
    (void)data;
    [self reject:parser];
}

- (NSData *)parser:(NSXMLParser *)parser resolveExternalEntityName:(NSString *)name systemID:(NSString *)systemID {
    (void)name;
    (void)systemID;
    [self reject:parser];
    return nil;
}

- (void)parser:(NSXMLParser *)parser parseErrorOccurred:(NSError *)parseError {
    (void)parser;
    (void)parseError;
    _invalid = YES;
}

- (NSDictionary<NSString *, NSString *> *)noticeWithChinese:(BOOL)chinese {
    if (_invalid || !_sawRecall || _elements.count != 0 || _fieldName) return nil;
    NSString *session = _values[@"session"];
    NSString *replacement = _values[@"replacemsg"];
    if (session.length == 0 || session.length > kMaximumSessionLength ||
        replacement.length == 0 || replacement.length > kMaximumNoticeLength ||
        [replacement stringByTrimmingCharactersInSet:NSCharacterSet.whitespaceAndNewlineCharacterSet].length == 0 ||
        [replacement containsString:@"]]>"]) return nil;
    NSMutableCharacterSet *forbidden = [NSCharacterSet.controlCharacterSet mutableCopy];
    [forbidden formUnionWithCharacterSet:NSCharacterSet.whitespaceAndNewlineCharacterSet];
    [forbidden addCharactersInString:@"<>&\"'"];
    if ([session rangeOfCharacterFromSet:forbidden].location != NSNotFound) return nil;

    NSString *identifier = _values[@"newmsgid"] ?: _values[@"newsvrid"] ?: _values[@"msgid"];
    uint64_t messageID = 0;
    if (!ParsePositiveID(identifier, &messageID)) return nil;
    if (_values[@"newmsgid"] && _values[@"newsvrid"]) {
        uint64_t alternate = 0;
        if (!ParsePositiveID(_values[@"newsvrid"], &alternate) || alternate != messageID) return nil;
    }
    NSString *suffix = chinese ? @"（已阻止本机撤回）" : @" (recall blocked on this Mac)";
    return @{@"session": session,
             @"messageID": [NSString stringWithFormat:@"%llu", (unsigned long long)messageID],
             @"text": [replacement stringByAppendingString:suffix]};
}

@end

NSDictionary<NSString *, NSString *> *WCTParseRecallNotice(NSData *xml, BOOL chinese) {
    if (xml.length == 0 || xml.length > kMaximumXMLBytes) return nil;
    NSString *source = [[NSString alloc] initWithData:xml encoding:NSUTF8StringEncoding];
    if (!source) return nil;
    if ([source hasPrefix:@"\uFEFF"]) source = [source substringFromIndex:1];
    const NSRange markup = [source rangeOfString:@"<"];
    if (markup.location == NSNotFound) return nil;
    NSString *prefix = [source substringToIndex:markup.location];
    if ([prefix stringByTrimmingCharactersInSet:NSCharacterSet.whitespaceAndNewlineCharacterSet].length &&
        !IsSenderPrefix(prefix)) return nil;
    source = [source substringFromIndex:markup.location];
    // The transport bytes were validated as UTF-8 above. Do not let an XML
    // declaration reinterpret the same bytes using a different character set.
    if ([source hasPrefix:@"<?xml"]) {
        const NSRange end = [source rangeOfString:@"?>"];
        if (end.location == NSNotFound) return nil;
        NSString *declaration = [source substringToIndex:NSMaxRange(end)];
        NSRegularExpression *encoding = [NSRegularExpression regularExpressionWithPattern:
            @"\\bencoding\\s*=\\s*(['\"])(.*?)\\1" options:0 error:nil];
        NSTextCheckingResult *match = [encoding firstMatchInString:declaration options:0
                                                           range:NSMakeRange(0, declaration.length)];
        if (match) {
            NSString *name = [[declaration substringWithRange:[match rangeAtIndex:2]] uppercaseString];
            if (![name isEqualToString:@"UTF-8"] && ![name isEqualToString:@"UTF8"]) return nil;
        }
    }
    // Reject declarations before invoking the XML parser. Predefined XML and
    // numeric character references remain available; custom entities do not.
    for (NSString *declaration in @[@"<!DOCTYPE", @"<!ENTITY"]) {
        if ([source rangeOfString:declaration options:NSCaseInsensitiveSearch].location != NSNotFound) return nil;
    }
    NSXMLParser *parser = [[NSXMLParser alloc] initWithData:[source dataUsingEncoding:NSUTF8StringEncoding]];
    parser.shouldResolveExternalEntities = NO;
    parser.shouldProcessNamespaces = NO;
    WCTRecallXMLParser *delegate = [[WCTRecallXMLParser alloc] init];
    parser.delegate = delegate;
    if (![parser parse]) return nil;
    return [delegate noticeWithChinese:chinese];
}
