// SPDX-License-Identifier: MIT
#import <Foundation/Foundation.h>
#import <objc/runtime.h>
#include <dlfcn.h>
#include <stdio.h>
#include <stdlib.h>

static unsigned registrations;
static void Spy4(id self, SEL command, id observer, SEL selector, NSString *name, NSString *object) {
    (void)self; (void)command; (void)observer; (void)selector; (void)name; (void)object;
    registrations++;
}
static void Spy5(id self, SEL command, id observer, SEL selector, NSString *name, NSString *object,
                 NSNotificationSuspensionBehavior behavior) {
    (void)behavior;
    Spy4(self, command, observer, selector, name, object);
}

static void *Load(NSString *path) {
    void *handle = dlopen(path.fileSystemRepresentation, RTLD_NOW | RTLD_LOCAL);
    if (!handle) { fprintf(stderr, "dlopen: %s\n", dlerror()); exit(2); }
    return handle;
}

int main(int argc, const char **argv) {
    @autoreleasepool {
        if (argc != 3) return 2;
        NSString *resources = [NSBundle.mainBundle.bundlePath stringByAppendingPathComponent:@"Contents/Resources"];
        NSString *plugin = [resources stringByAppendingPathComponent:@"WeChatTool/WeChatTool.dylib"];
        NSString *fixture = [resources stringByAppendingPathComponent:@"wechat.dylib"];
        NSString *mode = @(argv[1]);
        if ([mode isEqualToString:@"sharing-filter"]) {
            Class type = NSDistributedNotificationCenter.class;
            SEL four = @selector(addObserver:selector:name:object:);
            SEL five = @selector(addObserver:selector:name:object:suspensionBehavior:);
            class_replaceMethod(type, four, (IMP)Spy4, method_getTypeEncoding(class_getInstanceMethod(type, four)));
            class_replaceMethod(type, five, (IMP)Spy5, method_getTypeEncoding(class_getInstanceMethod(type, five)));
        }
        void *library;
        if ([mode isEqualToString:@"preloaded"]) {
            library = Load(fixture);
            Load(plugin);
        } else if ([mode isEqualToString:@"baseline"]) {
            library = Load(fixture);
        } else {
            Load(plugin);
            library = Load(fixture);
            if ([mode isEqualToString:@"twice"])
                Load([resources stringByAppendingPathComponent:@"WeChatTool/Second.dylib"]);
        }
        typedef bool (*Predicate)(const int *);
        Predicate predicate = (Predicate)dlsym(library, "is_revoke");
        if (!predicate) return 2;
        if ([mode isEqualToString:@"sharing-filter"]) {
            // No notifications are posted, and original registration methods
            // have been replaced with in-process spies: no real app receives data.
            NSDistributedNotificationCenter *center = NSDistributedNotificationCenter.defaultCenter;
            NSObject *observer = [NSObject new];
            registrations = 0;
            for (NSString *name in @[@"wechat_share_to_wechat_channel", @"WeChatMacShare_Notification"]) {
                [center addObserver:observer selector:@selector(description) name:name object:nil];
                [center addObserver:observer selector:@selector(description) name:name object:nil
                    suspensionBehavior:NSNotificationSuspensionBehaviorDrop];
            }
            if (registrations != 0) return 3;
            [center addObserver:observer selector:@selector(description) name:@"local.wechattool.fixture" object:nil];
            [center addObserver:observer selector:@selector(description) name:@"local.wechattool.fixture" object:nil
                suspensionBehavior:NSNotificationSuspensionBehaviorDrop];
            if (registrations != 2) return 4;
        }
        int record[4] = {0, 0, 0, 10002};
        const int actual = predicate(record) ? 1 : 0;
        const int expected = atoi(argv[2]);
        if (actual != expected) {
            fprintf(stderr, "predicate: expected %d, got %d\n", expected, actual);
            return 1;
        }
        record[3] = 0;
        if (predicate(record)) return 1;
        record[3] = 10000;
        if (predicate(record)) return 1;
        return 0;
    }
}
