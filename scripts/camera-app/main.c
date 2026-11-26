// Runs a command as the "Lip Dictation" app, so macOS asks for camera access for this app.
//
// macOS grants camera access to the "responsible" app of a process. Under Claude Desktop that is the
// Claude Code helper bundle, which declares no camera use, so the camera is refused without a prompt.
// This launcher restarts itself with responsibility disclaimed (making this bundle, whose Info.plist
// has NSCameraUsageDescription, the responsible app) and then runs the command as its child.
//   lip-dictation <command> [args...]
#include <errno.h>
#include <limits.h>
#include <mach-o/dyld.h>
#include <signal.h>
#include <spawn.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/wait.h>
#include <unistd.h>

extern char **environ;
// Private libSystem API (macOS 10.14+), also used by Chromium and Claude Desktop for the same purpose.
int responsibility_spawnattrs_setdisclaim(posix_spawnattr_t *attrs, int disclaim);

static pid_t child;

static void forward(int sig) {
    if (child > 0) kill(child, sig);
}

int main(int argc, char **argv) {
    if (argc < 2) {
        fprintf(stderr, "usage: %s command [args...]\n", argv[0]);
        return 64;
    }
    posix_spawnattr_t attrs;
    posix_spawnattr_init(&attrs);
    char **command = argv + 1;
    char self[PATH_MAX];
    uint32_t size = sizeof self;
    if (getenv("LIP_DICTATION_DISCLAIMED")) {
        unsetenv("LIP_DICTATION_DISCLAIMED");
    } else if (_NSGetExecutablePath(self, &size) == 0) {
        setenv("LIP_DICTATION_DISCLAIMED", "1", 1);
        responsibility_spawnattrs_setdisclaim(&attrs, 1);
        argv[0] = self;
        command = argv;
    }

    signal(SIGTERM, forward);
    signal(SIGINT, forward);
    signal(SIGHUP, forward);
    int error = posix_spawnp(&child, command[0], NULL, &attrs, command, environ);
    if (error) {
        fprintf(stderr, "lip-dictation: cannot run %s: %s\n", command[0], strerror(error));
        return 127;
    }

    // Stop the child when our caller dies (for example a reloaded Claude Code), as a direct child would.
    pid_t parent = getppid();
    int status = 0;
    for (;;) {
        pid_t done = waitpid(child, &status, WNOHANG);
        if (done == child) break;
        if (done < 0 && errno != EINTR) return 1;
        if (getppid() != parent) {
            kill(child, SIGTERM);
            parent = getppid();
        }
        usleep(200000);
    }
    return WIFEXITED(status) ? WEXITSTATUS(status) : 128 + WTERMSIG(status);
}
