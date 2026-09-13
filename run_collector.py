from collector.gui import main

if __name__ == "__main__":
    import sys
    if len(sys.argv) == 3 and sys.argv[1] == '--synthetic-smoke-report':
        # Explicit isolated test mode: temporary sources/state and blocked network.
        import unittest
        from tools.native_gui_smoke import NativeGuiSmoke
        with open(sys.argv[2], 'w', encoding='utf-8') as report:
            result = unittest.TextTestRunner(stream=report, verbosity=2).run(
                unittest.defaultTestLoader.loadTestsFromTestCase(NativeGuiSmoke))
        raise SystemExit(0 if result.wasSuccessful() else 1)
    main()
