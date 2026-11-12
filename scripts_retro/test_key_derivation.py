import os
import hashlib
import itertools
import pylibsrtp

DEVICE_TOKEN = "c28e1437ef0ff27b3df330bd0762574c"
CLIENT_TOKEN = "6e842a87dd6fdfdd1bbc24278a9e4586"

with open(os.path.join(os.path.dirname(__file__), "srtp_pair2.txt")) as f:
    cipher_hex = f.readline().strip()
    plain_hex = f.readline().strip()

cipher = bytes.fromhex(cipher_hex)
expected_plain = bytes.fromhex(plain_hex)

print("cipher len:", len(cipher), "expected plain len:", len(expected_plain))
print("RTP header (first 12 bytes):", cipher[:12].hex())


def md5(s):
    return hashlib.md5(s.encode()).digest()


def try_combo(order1, order2, profile_name, profile_const):
    h1 = md5(order1)  # -> master_key
    h2 = md5(order2)  # -> master_salt (first 14 bytes)
    master_key = h1
    master_salt = h2[:14]
    try:
        policy = pylibsrtp.Policy(
            key=master_key + master_salt,
            ssrc_type=pylibsrtp.Policy.SSRC_ANY_INBOUND,
            srtp_profile=profile_const,
        )
        session = pylibsrtp.Session(policy)
        result = session.unprotect(cipher)
        match = result == expected_plain
        print(f"[{profile_name}] order=({order1[:8]}..,{order2[:8]}..) -> len={len(result)} MATCH={match}")
        if match:
            print("  !!! FOUND MATCHING KEY DERIVATION !!!")
            print("  master_key =", master_key.hex())
            print("  master_salt=", master_salt.hex())
        return match
    except Exception as e:
        print(f"[{profile_name}] order=({order1[:8]}..,{order2[:8]}..) -> ERROR: {e}")
        return False


combos = [
    (DEVICE_TOKEN + CLIENT_TOKEN, CLIENT_TOKEN + DEVICE_TOKEN),
    (CLIENT_TOKEN + DEVICE_TOKEN, DEVICE_TOKEN + CLIENT_TOKEN),
]

profiles = {
    "AES128_CM_SHA1_80": pylibsrtp.Policy.SRTP_PROFILE_AES128_CM_SHA1_80,
    "AES128_CM_SHA1_32": pylibsrtp.Policy.SRTP_PROFILE_AES128_CM_SHA1_32,
}

found = False
for order1, order2 in combos:
    for pname, pconst in profiles.items():
        if try_combo(order1, order2, pname, pconst):
            found = True

if not found:
    print("\nNo exact match found with simple combos. Will try more.")
