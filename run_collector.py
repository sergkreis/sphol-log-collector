from collector.gui import main

if __name__ == "__main__":
    import sys
    if len(sys.argv) == 3 and sys.argv[1] == '--synthetic-smoke-report':
        # Explicit isolated test mode: temporary sources/state and blocked network.
        import unittest
        from tools.native_gui_smoke import NativeGuiSmoke
        from tools.native_single_smoke import NativeSingleSmoke
        from tools.connection_preview import NativeConnectionSmoke
        from tools.native_pairing_smoke import RecoveryTk
        from tools.native_site_code_smoke import NativeSiteCodeSmoke
        from tools.native_compact_smoke import NativeCompactSmoke
        from tools.native_recovery_smoke import RecoveryIsolation
        from tools.native_scheduler_smoke import SchedulerTests
        from tools.native_fault_smoke import NativeFaultSmoke
        with open(sys.argv[2], 'w', encoding='utf-8') as report:
            result = unittest.TextTestRunner(stream=report, verbosity=2).run(
                unittest.TestSuite(unittest.defaultTestLoader.loadTestsFromTestCase(case)
                                   for case in (NativeGuiSmoke, NativeSingleSmoke, NativeConnectionSmoke, RecoveryTk, NativeSiteCodeSmoke, NativeCompactSmoke, RecoveryIsolation, SchedulerTests, NativeFaultSmoke)))
        raise SystemExit(0 if result.wasSuccessful() else 1)
    if len(sys.argv) == 6 and sys.argv[1] == '--apply-update':
        from collector.updater import helper
        try:
            helper(*sys.argv[2:])
        except Exception:
            raise SystemExit(1)
        raise SystemExit(0)
    if len(sys.argv) != 1:
        raise SystemExit(2)
    main()
