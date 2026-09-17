// SPDX-License-Identifier: MIT
// Disposable sandbox fixture. Never execute its original-ID source bundle.
#import <Foundation/Foundation.h>
#include <dlfcn.h>
#include <fcntl.h>
#include <stdio.h>
#include <sys/file.h>
#include <unistd.h>

static void Require(BOOL condition, NSString *message) {
    if (!condition) { fprintf(stderr, "%s\n", message.UTF8String); exit(2); }
}

static void Print(NSDictionary *value) {
    NSData *data = [NSJSONSerialization dataWithJSONObject:value options:NSJSONWritingSortedKeys error:NULL];
    Require(data != nil, @"JSON encoding failed");
    fwrite(data.bytes, 1, data.length, stdout); fputc('\n', stdout); fflush(stdout);
}

static BOOL Identity(NSString *value) {
    NSString *prefix = @"local.wechattool.wechat.";
    if (![value hasPrefix:prefix] || value.length != prefix.length + 32) return NO;
    NSCharacterSet *invalid = [[NSCharacterSet characterSetWithCharactersInString:@"0123456789abcdef"] invertedSet];
    return [[value substringFromIndex:prefix.length] rangeOfCharacterFromSet:invalid].location == NSNotFound;
}

static NSString *OwnDirectory(NSString *base, NSString *identifier) {
    Require([base containsString:identifier], @"Refusing filesystem access outside this generated container");
    NSString *directory = [base stringByAppendingPathComponent:@"WeChatToolFixture"];
    NSError *error = nil;
    Require([NSFileManager.defaultManager createDirectoryAtPath:directory withIntermediateDirectories:YES
                  attributes:nil error:&error], error.description ?: @"Cannot create fixture directory");
    return directory;
}

int main(int argc, const char **argv) {
    alarm(15);
    @autoreleasepool {
        Require(argc >= 3, @"Expected action and marker");
        NSString *action = @(argv[1]), *marker = @(argv[2]);
        NSString *identifier = NSBundle.mainBundle.bundleIdentifier;
#if WCT_INSTANCE_HELPER
        Require(argc == 4, @"Expected inherited parent identifier");
        NSString *parent = @(argv[3]);
        Require(Identity(parent) && [NSHomeDirectory() containsString:parent], @"Helper did not inherit parent home");
        // Check both access to the parent's preferences and the helper's own
        // default domain, despite its identical bundle ID in the two copies.
        NSUserDefaults *parentDefaults = [[NSUserDefaults alloc] initWithSuiteName:parent];
        NSUserDefaults *defaults = NSUserDefaults.standardUserDefaults;
        NSString *key = @"WCTInstanceFixtureIdentity";
        id before = [defaults objectForKey:key];
        if ([action isEqualToString:@"write"]) [defaults setObject:marker forKey:key];
        if ([action isEqualToString:@"cleanup"]) [defaults removeObjectForKey:key];
        Require([defaults synchronize], @"Cannot synchronize helper defaults");
        id parentValue = [parentDefaults objectForKey:key];
        if (![action isEqualToString:@"cleanup"])
            Require([parentValue isEqual:marker] && [[defaults objectForKey:key] isEqual:marker], @"Helper preferences crossed identities");
        Print(@{@"bundle_id": identifier, @"home": NSHomeDirectory(), @"parent_value": parentValue ?: NSNull.null,
                @"previous": before ?: NSNull.null, @"value": [defaults objectForKey:key] ?: NSNull.null});
#else
        Require(Identity(identifier), @"Original-ID source fixture must never be executed");
        Require([NSHomeDirectory() containsString:identifier], @"Main fixture lacks its independent sandbox");
        NSString *resources = [NSBundle.mainBundle.bundlePath stringByAppendingPathComponent:@"Contents/Resources"];
        void *library = dlopen([[resources stringByAppendingPathComponent:@"wechat.dylib"] fileSystemRepresentation], RTLD_NOW | RTLD_LOCAL);
        Require(library != NULL, @"Cannot load synthetic predicate");
        bool (*predicate)(const int *) = (bool (*)(const int *))dlsym(library, "is_revoke");
        Require(predicate != NULL, @"Synthetic predicate missing");
        int record[4] = {0, 0, 0, 10002};
        int actual = predicate(record) ? 1 : 0;
        Require(actual == ([action isEqualToString:@"disabled"] ? 1 : 0), @"Plugin activation differs from expected state");

        NSString *support = NSSearchPathForDirectoriesInDomains(NSApplicationSupportDirectory, NSUserDomainMask, YES).firstObject;
        NSString *privateDirectory = OwnDirectory(support, identifier);
        NSString *groupID = [[NSBundle.mainBundle objectForInfoDictionaryKey:@"TeamIdentifier"] stringByAppendingString:identifier];
        NSURL *groupURL = [NSFileManager.defaultManager containerURLForSecurityApplicationGroupIdentifier:groupID];
        Require(groupURL != nil, @"Independent group container unavailable");
        NSString *groupDirectory = OwnDirectory(groupURL.path, identifier);
        NSString *privateFile = [privateDirectory stringByAppendingPathComponent:@"marker.txt"];
        NSString *groupFile = [groupDirectory stringByAppendingPathComponent:@"marker.txt"];
        NSString *lockFile = [privateDirectory stringByAppendingPathComponent:@"process.lock"];
        NSUserDefaults *defaults = NSUserDefaults.standardUserDefaults;
        NSString *key = @"WCTInstanceFixtureIdentity";
        NSMutableDictionary *result = [@{@"bundle_id": identifier, @"home": NSHomeDirectory(),
            @"support": support, @"group": groupURL.path, @"predicate": @(actual)} mutableCopy];

        if ([action isEqualToString:@"hold"] || [action isEqualToString:@"try-lock"]) {
            int fd = open(lockFile.fileSystemRepresentation, O_CREAT | O_RDWR, 0600);
            Require(fd >= 0, @"Cannot open own fixture lock");
            BOOL acquired = flock(fd, LOCK_EX | LOCK_NB) == 0;
            result[@"locked"] = @(acquired); Print(result);
            if ([action isEqualToString:@"hold"]) {
                Require(acquired, @"Concurrent independent copy could not acquire its own lock");
                char release[32]; Require(fgets(release, sizeof(release), stdin) != NULL, @"Lock holder not released");
            }
            close(fd); return 0;
        }
        if ([action isEqualToString:@"write"]) {
            Require([marker writeToFile:privateFile atomically:YES encoding:NSUTF8StringEncoding error:NULL], @"Private write failed");
            Require([marker writeToFile:groupFile atomically:YES encoding:NSUTF8StringEncoding error:NULL], @"Group write failed");
            [defaults setObject:marker forKey:key];
            Require([defaults synchronize], @"Main defaults synchronization failed");
        }
        if (![action isEqualToString:@"cleanup"]) {
            Require([[NSString stringWithContentsOfFile:privateFile encoding:NSUTF8StringEncoding error:NULL] isEqualToString:marker], @"Private marker mismatch");
            Require([[NSString stringWithContentsOfFile:groupFile encoding:NSUTF8StringEncoding error:NULL] isEqualToString:marker], @"Group marker mismatch");
            Require([[defaults objectForKey:key] isEqual:marker], @"Main preferences crossed identities");
        }

        NSTask *helper = [NSTask new];
        helper.executableURL = [NSURL fileURLWithPath:[NSBundle.mainBundle.bundlePath
            stringByAppendingPathComponent:@"Contents/Helpers/FixtureHelper.app/Contents/MacOS/FixtureHelper"]];
        helper.arguments = @[[action isEqualToString:@"disabled"] ? @"read" : action, marker, identifier];
        NSPipe *output = [NSPipe pipe]; helper.standardOutput = output;
        NSError *error = nil;
        Require([helper launchAndReturnError:&error], error.description ?: @"Cannot start inherited helper");
        NSData *helperData = [output.fileHandleForReading readDataToEndOfFile];
        [helper waitUntilExit]; Require(helper.terminationStatus == 0, @"Inherited helper failed");
        NSDictionary *helperResult = [NSJSONSerialization JSONObjectWithData:helperData options:0 error:NULL];
        Require([helperResult isKindOfClass:NSDictionary.class], @"Helper did not report JSON");
        result[@"helper"] = helperResult;
        if ([action isEqualToString:@"cleanup"]) {
            [defaults removeObjectForKey:key]; Require([defaults synchronize], @"Defaults cleanup failed");
            Require([NSFileManager.defaultManager removeItemAtPath:privateDirectory error:NULL], @"Private fixture cleanup failed");
            Require([NSFileManager.defaultManager removeItemAtPath:groupDirectory error:NULL], @"Group fixture cleanup failed");
            result[@"cleaned"] = @YES;
        }
        Print(result);
#endif
    }
    return 0;
}
