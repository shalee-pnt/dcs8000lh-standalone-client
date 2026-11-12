function hex(buf, len) {
    var bytes = buf.readByteArray(len);
    var arr = new Uint8Array(bytes);
    var s = "";
    for (var i = 0; i < arr.length; i++) {
        s += (arr[i] < 16 ? "0" : "") + arr[i].toString(16);
    }
    return s;
}

function waitForModule(name, cb) {
    var mod = Process.findModuleByName(name);
    if (mod) {
        cb(mod);
        return;
    }
    var interval = setInterval(function () {
        var m = Process.findModuleByName(name);
        if (m) {
            clearInterval(interval);
            cb(m);
        }
    }, 500);
}

waitForModule("libsa.so", function (mod) {
    var unprotect = mod.findExportByName("srtp_unprotect");
    var protect = mod.findExportByName("srtp_protect");
    console.log("[*] srtp_unprotect @ " + unprotect + "  srtp_protect @ " + protect);

    if (unprotect) {
        Interceptor.attach(unprotect, {
            onEnter: function (args) {
                this.buf = args[1];
                this.lenPtr = args[2];
                this.inLen = this.lenPtr.readS32();
            },
            onLeave: function (retval) {
                var rc = retval.toInt32();
                var outLen = this.lenPtr.readS32();
                if (rc === 0 && outLen > 0) {
                    send({ kind: "rtp", len: outLen }, this.buf.readByteArray(outLen));
                } else {
                    send({ kind: "unprotect_fail", rc: rc, inLen: this.inLen });
                }
            }
        });
    }

    if (protect) {
        Interceptor.attach(protect, {
            onEnter: function (args) {
                this.buf = args[1];
                this.lenPtr = args[2];
            },
            onLeave: function (retval) {
                // not forwarding outgoing; just counting
            }
        });
    }

    console.log("[*] srtp hooks installed. Start live view now.");
});
