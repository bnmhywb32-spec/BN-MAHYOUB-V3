import 'package:flutter_test/flutter_test.dart';
import 'package:integration_test/integration_test.dart';
import 'package:platform_proof/main.dart' as app;

void main() {
  IntegrationTestWidgetsFlutterBinding.ensureInitialized();

  testWidgets('Sale, rollback and replay survive database reopen', (_) async {
    // A dedicated database per test run keeps retries reproducible.
    final path = 'contract-${DateTime.now().microsecondsSinceEpoch}.db';
    try {
      expect(await app.proof(path: path),
        'PASS: durable sale saved; relaunch to verify persistence');
      expect(await app.proof(path: path),
        'PASS: persisted sale verified after relaunch');
    } finally {
      await app.factory().deleteDatabase(path);
    }
  });
}
