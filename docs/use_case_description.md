# How Mobile TestStation Uncovered Hidden Bugs and Boosted Test Coverage

After building and deploying Mobile TestStation, the next step was to integrate it into our CI/CD pipeline. 
All of our Android devices have been connected to the TestStation, x86 and Arm64 emulators have been setup to increase coverage.
The emulators are used to test on different architecture and also to increase the test coverage on different Android versions, which is difficult on real devices.

In this article we explore how Mobile TestStation helped us to uncover hidden bugs and how we manged to increase the test coverage.

## Use Case: Automated Testing of Mobile Protection

At Denuvo, we develop Mobile Protection technology designed to safeguard mobile applications against hacking, tampering, and cheating. 
The protection is injected directly into customer apps after the build process by means of binary rewriting -- no source code modification needed.
The added layers of security detect debugging attempts, code manipulation, and integrity violations.

Given the complexity of the protection and its deep integration with application internals, thorough testing is critical. 
The Mobile TestStation provides the ideal playground especially for startup testing. In-app testing is not supported by the TestStation as something 
like a bot or other automation would need to play the game to ensure that all protected code gets executed.

For testing the full protection, we crafted specific custom applications where all security code is executed on startup. 
For this use case, the teststation became invaluable to run the application on a wide range of devices and emulators quickly.

In our CI/CD pipeline the protection is built each night and automatically tested.
The Mobile TestStation allows way more and faster testing coverage across devices, operating system version, and emulators than what would be possible to achieve by manually plugging devices or spinning up emulators. This helped us to detected some hidden bugs... read on for the gory details!

### 🐛 Bug 1: Emulator AES Inconsistency

With the feature of the TestStation to test on different Android versions via emulators, we
noticed that applications with AES instructions (using the Arm64 instruction set extension) behave differently on new Android emulator versions
(> 35.1.20). This issue was isolated quickly only thanks to the scalable architecture and wide coverage of the TestStation.

The bug itself has been reported to [Google](https://issuetracker.google.com/issues/388718417).
The translation layer is part of an application execution flow on an emulator that allows an app to run on non-Android systems like a Linux desktop by translating Android specific API and runtime calls into equivalent host operating system calls. This results in faster execution than a full virtualisation.

The translation layer wrongly identified the host as supporting AES instructions. Hence, the emulator enabled support for AES instructions. However, on execution the process 
dies with the Error `CHECK failed: HostPlatform::kHasAES`.

This happened due to Google removing features to work on cross-platform snapshots as stated in the bug report's discussion:
```
We purposefully limited the features so that we can work on cross platform snapshot load.
If you reply on the old behavior, please add "-xts" to the emulator commandline.
```

As the change for AES was not documented, the feature removal was missed, and the TestStation gave us
an early way to detect it and have an answer ready in case customers report this issue to us.


### 🐛 Bug 2: Popen Misuse and Platform-Specific Behavior

Before releasing a new feature, exhaustive testing is essential. 
For our implementation of the `popen` syscall in assembly — part of our custom syscalls designed to prevent attackers from hooking into native libraries like libc — the Mobile TestStation proved invaluable.
Thanks to the multitude of connected physical devices and virtual emulators, we identified a reproducible crash specific to Xiaomi devices. The application hung indefinitely on startup.

[popen](https://man7.org/linux/man-pages/man3/popen.3.html) on Linux-based systems opens a process, creates
a pipe, forks and invokes a command. The pipe is returned to the caller to be able to communicate with the
newly spawned process. Due to its complexity, popen is significantly more involved than basic `read` or `write` syscalls.

To support both, our custom and standard `popen` implementations while maintaining a consistent interface, we wrapped the original popen call.
The original signature, `FILE *popen(const char *command, const char *type);`, was adapted to `int popen(const char *command, const char *type);`.
Hence, both versions would return a file descriptor. This made it easier to enable or disable the custom feature transparently.

To bridge the gap between the `FILE*` and `int` interfaces, we used the following logic:
```C
FILE* stream = popen(cmd, "r");
if (stream == nullptr)
{
    return -1;
}

int stream_fd = fileno(stream); // after `fileno` the fd is still owned by the stream
if (stream_fd == -1)
{
    pclose(stream);
    return -1;
}
int fd = dup(stream_fd);
if (fd == -1)
{
    pclose(stream);
    return -1;
}
pclose(stream);
return fd;
```

Our intention was to duplicate the file descriptor, return it to the caller, and
immediately close the original `FILE*` stream, since it was no longer needed.
Subsequent logic would use the duplicate descriptor and handle closing it appropriately.

This worked reliably across all tested devices — except on Xiaomi. There, the first
`pclose` call blocked until the child process exited. As a result, by the time the
second `pclose` (in higher-level code) was called, there was nothing left to wait
for, and it would hang indefinitely.

The fix was to replace this pattern with a single custom function that executes
the process, reads the output, and closes everything in one go. This change ensured
consistent behavior across all platforms, simplified usage, and reduced code
complexity by returning the full process output as a single result.


## Why the teststation became invaluable to us

Unprotected customer applications are a critical resource. They cannot be freely shared with third party testing labs such as [Firebase Test Lab](https://firebase.google.com/docs/test-lab), as this conflicts with contractual obligations.

Given all devices are in full control and the configuration of each device or emulator is known, this makes troubleshooting very easy once a bug is found.
Developer can just grab the device for the day and perform analysis locally on his/her desk.


## Conclusion

With TestStation integrated into our mobile development lifecycle, we significantly increased our **test depth**, **platform coverage**, and **defect detection rate** -— all without relying on third-party testing services or cloud-based device farms.

By building a test infrastructure we fully control, we ensure that every device has well-known characteristics, and is stable as well as configurable.
Reliance on Mobile TestStation has enabled faster development iterations and more reliable tests.

- Written by Kurt Nistelberger and Johannes Schatteiner
