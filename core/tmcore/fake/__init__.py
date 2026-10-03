# SPDX-License-Identifier: AGPL-3.0-or-later
"""
Virtual iPhone for --fake-device (DESIGN §13.2): usbmux/lockdown/mobilebackup2/afc replacements backed by a
synthetic device image (fixtures/gen_ios_backup.py) that produce real encrypted backups and accept restores with
iOS-27 annotation semantics. Scenario switches: fake/scenarios/<name>.json, names identical to
app/Tests/Scenarios/<name>.jsonl. A fake run blocks every socket, AF_UNIX included (tmcore.netguard), so it can
never reach a real device. Owner: coreB.

    from tmcore.fake.gateway import FakeGateway      # what tmcore.steps.iphone.gateway() returns in fake mode
"""
