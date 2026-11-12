function hex(buf, len) {
    if (!buf || buf.isNull() || len <= 0) return "";
    try {
        var bytes = buf.readByteArray(len);
        var arr = new Uint8Array(bytes);
        var s = "";
        for (var i = 0; i < arr.length; i++) {
            s += (arr[i] < 16 ? "0" : "") + arr[i].toString(16);
        }
        return s;
    } catch (e) {
        return "<err:" + e + ">";
    }
}

function tryReadCString(ptr, maxLen) {
    try {
        return ptr.readCString(maxLen || 128);
    } catch (e) {
        return "<not a cstring>";
    }
}

function waitForModule(name, cb) {
    var mod = Process.findModuleByName(name);
    if (mod) { cb(mod); return; }
    var interval = setInterval(function () {
        var m = Process.findModuleByName(name);
        if (m) { clearInterval(interval); cb(m); }
    }, 500);
}

waitForModule("libsa.so", function (mod) {
    console.log("[*] libsa.so loaded, installing key/crypto hooks");

    function hookFn(name, nargs, fmt) {
        var addr = mod.findExportByName(name);
        if (!addr) {
            console.log("[!] " + name + " not found");
            return;
        }
        Interceptor.attach(addr, {
            onEnter: function (args) {
                this.args = [];
                for (var i = 0; i < nargs; i++) this.args.push(args[i]);
                console.log("\n>>> CALL " + name + "(" + fmt(this.args, null) + ")");
            },
            onLeave: function (retval) {
                console.log("<<< RET  " + name + " -> " + retval + "  [" + fmt(this.args, retval) + "]");
            }
        });
        console.log("[*] hooked " + name + " @ " + addr);
    }

    // sa_util_create_pair_key - guess signature: (const char* a, const char* b, unsigned char* out)
    var pairKeyAddr = mod.findExportByName("sa_util_create_pair_key");
    if (pairKeyAddr) {
        Interceptor.attach(pairKeyAddr, {
            onEnter: function (args) {
                this.a2 = args[2];
                this.a3 = args[3];
                console.log("\n>>> sa_util_create_pair_key ENTER" +
                    "\n    a0(cstr)=" + tryReadCString(args[0], 64) +
                    "\n    a1(cstr)=" + tryReadCString(args[1], 64) +
                    "\n    a2=" + args[2] + " a3=" + args[3]);
            },
            onLeave: function (retval) {
                console.log("<<< sa_util_create_pair_key LEAVE ret=" + retval +
                    "\n    a2 hex(32)=" + hex(this.a2, 32) +
                    "\n    a3 hex(32)=" + hex(this.a3, 32));
            }
        });
        console.log("[*] hooked sa_util_create_pair_key @ " + pairKeyAddr);
    } else {
        console.log("[!] sa_util_create_pair_key not found");
    }

    function tryDerefAndDump(ptr, label) {
        try {
            var p1 = ptr.readPointer();
            console.log("    " + label + " -> deref[0] ptr=" + p1 + " bytes=" + hex(p1, 48) + " ascii=" + tryAscii(p1, 48));
        } catch (e) {
            console.log("    " + label + " -> deref[0] failed: " + e);
        }
        try {
            var p2 = ptr.add(8).readPointer();
            console.log("    " + label + " -> deref[1] ptr=" + p2 + " bytes=" + hex(p2, 48) + " ascii=" + tryAscii(p2, 48));
        } catch (e) {}
    }

    function dumpBufArg(name) {
        var addr = mod.findExportByName(name);
        if (!addr) { console.log("[!] " + name + " not found"); return; }
        Interceptor.attach(addr, {
            onEnter: function (args) {
                this.a2 = args[2];
                this.a3 = args[3].toInt32();
                this.a4 = args[4];
                console.log("\n>>> " + name + " ENTER len=" + this.a3 +
                    "\n    a2(before) hex=" + hex(this.a2, Math.min(this.a3, 160)) +
                    "\n    a2(before) ascii=" + tryAscii(this.a2, Math.min(this.a3, 160)) +
                    "\n    a4 raw hex=" + hex(this.a4, 32));
            },
            onLeave: function (retval) {
                console.log("<<< " + name + " LEAVE ret=" + retval +
                    "\n    a2(after) hex=" + hex(this.a2, Math.min(this.a3, 160)) +
                    "\n    a4 raw hex(after)=" + hex(this.a4, 32));
                tryDerefAndDump(this.a4, "a4");
            }
        });
        console.log("[*] hooked " + name + " @ " + addr);
    }

    function tryAscii(buf, len) {
        try {
            var bytes = buf.readByteArray(len);
            var arr = new Uint8Array(bytes);
            var s = "";
            for (var i = 0; i < arr.length; i++) {
                var b = arr[i];
                s += (b >= 32 && b < 127) ? String.fromCharCode(b) : ".";
            }
            return s;
        } catch (e) {
            return "<err>";
        }
    }

    dumpBufArg("sa_util_encrypt");
    dumpBufArg("sa_util_decrypt");

    function hookHash(name) {
        var addr = mod.findExportByName(name);
        if (!addr) { console.log("[!] " + name + " not found"); return; }
        Interceptor.attach(addr, {
            onEnter: function (args) {
                this.a0 = args[0];
                this.a1 = args[1];
                this.a2 = args[2];
                console.log("\n>>> " + name + " ENTER" +
                    "\n    a0 cstr=" + tryReadCString(args[0], 80) + " hex=" + hex(args[0], 48) +
                    "\n    a1=" + args[1] +
                    "\n    a2=" + args[2]);
            },
            onLeave: function (retval) {
                console.log("<<< " + name + " LEAVE ret=" + retval +
                    "\n    a0 hex(after)=" + hex(this.a0, 48) +
                    "\n    a2 hex(after, as output?)=" + hex(this.a2, 32));
            }
        });
        console.log("[*] hooked " + name + " @ " + addr);
    }
    hookHash("sa_util_md5_hash");
    hookHash("sa_util_sha1_hash");
    hookHash("sa_util_create_ctrl_data");
    hookHash("sa_util_verify_peer_msg");

    var cmdHandler = mod.findExportByName("sa_command_handler");
    if (cmdHandler) {
        Interceptor.attach(cmdHandler, {
            onEnter: function (args) {
                console.log("\n>>> sa_command_handler args[0]=" + tryReadCString(args[0], 400) +
                    "\n    args[1]=" + tryReadCString(args[1], 400) +
                    "\n    args[2]=" + args[2] + " args[3]=" + args[3]);
            }
        });
        console.log("[*] hooked sa_command_handler @ " + cmdHandler);
    }
    var connDataSend = mod.findExportByName("sa_conn_data_send");
    if (connDataSend) {
        Interceptor.attach(connDataSend, {
            onEnter: function (args) {
                console.log("\n>>> sa_conn_data_send args[0]=" + args[0] + " args[1]=" + args[1] +
                    " args[2]=" + args[2] + " args[3]=" + args[3]);
            }
        });
        console.log("[*] hooked sa_conn_data_send @ " + connDataSend);
    }
    var connDataRecv = mod.findExportByName("sa_conn_data_recv");
    if (connDataRecv) {
        Interceptor.attach(connDataRecv, {
            onEnter: function (args) {
                console.log("\n>>> sa_conn_data_recv args[0]=" + args[0] + " args[1]=" + args[1] +
                    " args[2]=" + args[2] + " args[3]=" + args[3]);
            }
        });
        console.log("[*] hooked sa_conn_data_recv @ " + connDataRecv);
    }

    // srtp_crypto_policy_set_aes_cm_128_hmac_sha1_32(srtp_crypto_policy_t *p)
    ["srtp_crypto_policy_set_aes_cm_128_hmac_sha1_32",
     "srtp_crypto_policy_set_aes_cm_128_hmac_sha1_80",
     "srtp_crypto_policy_set_aes_cm_256_hmac_sha1_32",
     "srtp_crypto_policy_set_aes_cm_256_hmac_sha1_80"].forEach(function (name) {
        var addr = mod.findExportByName(name);
        if (!addr) return;
        Interceptor.attach(addr, {
            onLeave: function (retval) {
                console.log("\n>>> " + name + " called");
            }
        });
    });

    // srtp_create(srtp_t *session, const srtp_policy_t *policy)
    var srtpCreate = mod.findExportByName("srtp_create");
    if (srtpCreate) {
        Interceptor.attach(srtpCreate, {
            onEnter: function (args) {
                var policyPtr = args[1];
                try {
                    // srtp_policy_t layout (libsrtp2): ssrc (8 bytes: type+value), rtp policy struct, rtcp policy struct, key ptr, ...
                    // We just dump raw bytes around to eyeball the key pointer by scanning common offsets.
                    console.log("\n>>> srtp_create policy ptr=" + policyPtr);
                    for (var off = 0; off <= 96; off += 8) {
                        var val = policyPtr.add(off).readPointer();
                        console.log("    offset " + off + ": ptr=" + val);
                    }
                } catch (e) {
                    console.log("    (error reading policy: " + e + ")");
                }
            },
            onLeave: function (retval) {
                console.log("<<< srtp_create -> " + retval);
            }
        });
        console.log("[*] hooked srtp_create @ " + srtpCreate);
    }

    console.log("[*] All key/crypto hooks installed. Start live view now.");
});
