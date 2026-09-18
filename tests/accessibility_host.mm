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
        if (argc != 5) return 2;
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
        }
        using Recall = bool (*)(const int *);
        using Interface = int (*)(const void *);
        using Actions = bool (*)();
        Recall recall = reinterpret_cast<Recall>(dlsym(library, "is_revoke"));
        Interface interface = reinterpret_cast<Interface>(dlsym(library, "fixture_interface"));
        Actions actions = reinterpret_cast<Actions>(dlsym(library, "fixture_actions"));
        if (!recall || !interface || !actions) return 2;
        int record[4] = {0, 0, 0, 10002};
        const int actual[] = {recall(record) ? 1 : 0, interface(record), actions() ? 1 : 0};
        for (int index = 0; index < 3; ++index) {
            const int expected = atoi(argv[index + 2]);
            if (actual[index] != expected) {
                fprintf(stderr, "predicate %d: expected %d, got %d\n", index, expected, actual[index]);
                return 1;
            }
        }
        // The null-input guard must survive even when availability is enabled.
        if (interface(nullptr) != 0) return 3;
        record[3] = 0;
        if (recall(record)) return 4;
        return 0;
    }
}
