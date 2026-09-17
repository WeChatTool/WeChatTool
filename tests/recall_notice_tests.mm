#import <Foundation/Foundation.h>
#import "RecallNotice.h"

#include <stdio.h>
#include <stdlib.h>

static NSUInteger checks = 0;

static void Require(BOOL condition, const char *label) {
    ++checks;
    if (!condition) {
        fprintf(stderr, "FAIL: %s\n", label);
        exit(1);
    }
}

static NSString *Event(NSString *fields) {
    return [NSString stringWithFormat:@"<sysmsg type=\"revokemsg\"><revokemsg>%@</revokemsg></sysmsg>", fields];
}

static NSDictionary<NSString *, NSString *> *Parse(NSString *xml, BOOL chinese = NO) {
    return WCTParseRecallNotice([xml dataUsingEncoding:NSUTF8StringEncoding], chinese);
}

static NSString *Fields(NSString *identifier, NSString *replacement) {
    return [NSString stringWithFormat:@"<session>wxid_friend</session><newmsgid>%@</newmsgid><replacemsg>%@</replacemsg>",
                                     identifier, replacement];
}

int main(void) {
    @autoreleasepool {
        NSString *basic = Event(Fields(@"123", @"&quot;Alice&quot; recalled a message"));
        NSDictionary *notice = Parse(basic);
        Require(notice.count == 3, "exact return keys");
        Require([notice[@"session"] isEqualToString:@"wxid_friend"], "session preserved");
        Require([notice[@"messageID"] isEqualToString:@"123"], "message ID");
        Require([notice[@"text"] isEqualToString:@"\"Alice\" recalled a message (recall blocked on this Mac)"], "English notice");
        Require([Parse(Event(Fields(@"1", @"<![CDATA[\"张三🙂\" 撤回了一条消息]]>")), YES)[@"text"]
                 isEqualToString:@"\"张三🙂\" 撤回了一条消息（已阻止本机撤回）"], "Chinese Unicode CDATA");
        Require([Parse(Event(Fields(@"1", @"A &amp; B &lt;C&gt; &apos;D&apos; &#x1F642; &#65;")))[@"text"]
                 isEqualToString:@"A & B <C> 'D' 🙂 A (recall blocked on this Mac)"], "standard and numeric XML references");
        Require([Parse(Event(Fields(@"000123", @" Alice recalled a message \n")))[@"text"]
                 isEqualToString:@" Alice recalled a message \n (recall blocked on this Mac)"], "notice whitespace preserved");
        Require([Parse(Event(Fields(@"000123", @"Alice")))[@"messageID"] isEqualToString:@"123"], "canonical message ID");
        Require(Parse([@"wxid_sender:\n" stringByAppendingString:basic]) != nil, "ASCII sender prefix");
        Require(Parse([@"old.account-1:\r\n" stringByAppendingString:basic]) != nil, "CRLF sender prefix");
        Require(Parse([@" \t\n" stringByAppendingString:basic]) != nil, "leading whitespace");
        Require(Parse([@"\uFEFF" stringByAppendingString:basic]) != nil, "UTF-8 BOM");
        Require(Parse([@"<?xml version=\"1.0\" encoding=\"UTF-8\"?>" stringByAppendingString:basic]) != nil, "XML declaration");
        Require(Parse([@"<?xml version='1.0' encoding='utf-8'?>" stringByAppendingString:basic]) != nil, "UTF-8 declaration case");
        Require(Parse([@"<?xml version=\"1.0\" encoding=\"ISO-8859-1\"?>" stringByAppendingString:basic]) == nil, "encoding reinterpretation rejected");
        Require(Parse(Event([Fields(@"123", @"Alice") stringByAppendingString:@"<future><value>x</value></future>"])) != nil, "unrelated bounded extension");

        for (NSString *prefix in @[@"wxid_sender:", @"wxid_sender:\t\n", @"sender: ", @":\n", @"张三:\n", @"wrong text\n", @"sender:\nother\n"]) {
            Require(Parse([prefix stringByAppendingString:basic]) == nil, "invalid sender prefix");
        }
        for (NSString *identifier in @[@"", @"0", @"0000", @"-1", @"+1", @" 1", @"1 ", @"1\n", @"1.0", @"１２", @"18446744073709551616", @"100000000000000000000"]) {
            Require(Parse(Event(Fields(identifier, @"Alice"))) == nil, "invalid selected ID");
        }
        Require([Parse(Event(Fields(@"18446744073709551615", @"Alice")))[@"messageID"]
                 isEqualToString:@"18446744073709551615"], "maximum uint64");
        Require(Parse([basic stringByReplacingOccurrencesOfString:@"newmsgid" withString:@"newsvrid"]) != nil, "newsvrid supported");
        Require(Parse([basic stringByReplacingOccurrencesOfString:@"newmsgid" withString:@"msgid"]) != nil, "msgid fallback");
        Require(Parse(Event([Fields(@"123", @"Alice") stringByAppendingString:@"<msgid>0</msgid>"])) != nil, "old msgid does not override new ID");
        Require(Parse(Event([Fields(@"123", @"Alice") stringByAppendingString:@"<newsvrid>123</newsvrid>"])) != nil, "matching new ID aliases");
        Require(Parse(Event([Fields(@"123", @"Alice") stringByAppendingString:@"<newsvrid>124</newsvrid>"])) == nil, "conflicting new ID aliases");

        for (NSString *field in @[@"session", @"replacemsg", @"newmsgid", @"newsvrid", @"msgid"]) {
            NSString *extra = [NSString stringWithFormat:@"<%@>1</%@><%@>1</%@>", field, field, field, field];
            Require(Parse(Event([Fields(@"123", @"Alice") stringByAppendingString:extra])) == nil, "duplicate target field");
        }
        for (NSString *replacement in @[@"", @" \t\n", @"<name>Alice</name>", @"Alice]]&gt;", @"Alice]]&#62;", @"<![CDATA[Alice]]]]><![CDATA[>]]>", @"&unknown;"]) {
            Require(Parse(Event(Fields(@"1", replacement))) == nil, "missing actor or unsafe replacement");
        }
        Require(Parse(Event(@"<session>wxid_friend</session><newmsgid>123</newmsgid>")) == nil, "missing replacemsg");
        Require(Parse(Event(@"<session>wxid_friend</session><replacemsg>Alice</replacemsg>")) == nil, "missing ID");
        Require(Parse(Event(@"<newmsgid>1</newmsgid><replacemsg>Alice</replacemsg>")) == nil, "missing session");
        for (NSString *session in @[@"", @"a b", @"a\nb", @"a&#9;b", @"a&lt;b", @"a&gt;b", @"a&amp;b", @"a&quot;b", @"a&apos;b"]) {
            Require(Parse([basic stringByReplacingOccurrencesOfString:@"wxid_friend" withString:session]) == nil, "invalid session");
        }

        for (NSString *bad in @[
            [basic stringByReplacingOccurrencesOfString:@"type=\"revokemsg\"" withString:@"type=\"other\""],
            [basic stringByReplacingOccurrencesOfString:@"type=\"revokemsg\"" withString:@""],
            [basic stringByReplacingOccurrencesOfString:@"<revokemsg>" withString:@"<other>"],
            [basic stringByReplacingOccurrencesOfString:@"<newmsgid>" withString:@"<newmsgid extra=\"1\">"],
            [basic stringByReplacingOccurrencesOfString:@"<session>" withString:@"<wrapper><session>"],
            [basic stringByReplacingOccurrencesOfString:@"</session>" withString:@"</session></wrapper>"],
            [basic stringByAppendingString:basic],
            [basic substringToIndex:basic.length - 1],
            Event([@"unexpected" stringByAppendingString:Fields(@"1", @"Alice")]),
            [basic stringByReplacingOccurrencesOfString:@"<sysmsg " withString:@"<sysmsg xmlns=\"evil\" "],
            [@"<?unsafe run?>" stringByAppendingString:basic],
            [basic stringByReplacingOccurrencesOfString:@"</sysmsg>" withString:@"<revokemsg/></sysmsg>"],
        ]) Require(Parse(bad) == nil, "malformed structure");

        for (NSString *declaration in @[
            @"<!DOCTYPE sysmsg SYSTEM 'file:///must-not-be-opened'>",
            @"<!DOCTYPE sysmsg [<!ENTITY actor SYSTEM 'file:///must-not-be-opened'>]>",
            @"<!DOCTYPE sysmsg [<!ENTITY actor SYSTEM 'https://invalid.example/actor'>]>",
            @"<!DOCTYPE sysmsg [<!ENTITY a 'expansion'><!ENTITY b '&a;&a;'>]>",
            @"<!DOCTYPE sysmsg [<!ENTITY % actor SYSTEM 'file:///must-not-be-opened'>%actor;]>",
            @"<!ENTITY actor 'Alice'>",
        ]) Require(Parse([declaration stringByAppendingString:basic]) == nil, "DTD and custom entities rejected");

        NSString *session256 = [@"s" stringByPaddingToLength:256 withString:@"s" startingAtIndex:0];
        Require(Parse([basic stringByReplacingOccurrencesOfString:@"wxid_friend" withString:session256]) != nil, "session length boundary");
        Require(Parse([basic stringByReplacingOccurrencesOfString:@"wxid_friend" withString:[session256 stringByAppendingString:@"s"]]) == nil, "session over limit");
        NSString *notice2048 = [@"x" stringByPaddingToLength:2048 withString:@"x" startingAtIndex:0];
        Require(Parse(Event(Fields(@"1", notice2048))) != nil, "replacement length boundary");
        Require(Parse(Event(Fields(@"1", [notice2048 stringByAppendingString:@"x"]))) == nil, "replacement over limit");
        NSString *padding = [@" " stringByPaddingToLength:65536 - [basic lengthOfBytesUsingEncoding:NSUTF8StringEncoding]
                                               withString:@" " startingAtIndex:0];
        NSString *maximum = [basic stringByAppendingString:padding];
        Require(Parse(maximum) != nil, "64 KiB input boundary");
        Require(Parse([maximum stringByAppendingString:@" "]) == nil, "input over 64 KiB");
        NSMutableString *deep = [NSMutableString string];
        for (NSUInteger index = 0; index < 17; ++index) [deep appendString:@"<future>"];
        for (NSUInteger index = 0; index < 17; ++index) [deep appendString:@"</future>"];
        Require(Parse(Event([Fields(@"1", @"Alice") stringByAppendingString:deep])) == nil, "depth limit");
        NSMutableString *many = [NSMutableString string];
        for (NSUInteger index = 0; index < 257; ++index) [many appendString:@"<future/>"];
        Require(Parse(Event([Fields(@"1", @"Alice") stringByAppendingString:many])) == nil, "element limit");
        const uint8_t invalidUTF8[] = {0xff, 0xfe, 0x3c, 0x00};
        Require(WCTParseRecallNotice([NSData dataWithBytes:invalidUTF8 length:sizeof(invalidUTF8)], NO) == nil, "invalid UTF-8");
        Require(WCTParseRecallNotice([NSData data], NO) == nil, "empty input");
        printf("%lu recall notice checks passed\n", (unsigned long)checks);
    }
    return 0;
}
