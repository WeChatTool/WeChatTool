// SPDX-License-Identifier: MIT
#import <Foundation/Foundation.h>
#include <dlfcn.h>
#include <stdio.h>
#include <stdlib.h>

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
