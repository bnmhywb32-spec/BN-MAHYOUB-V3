// Synthetic reference implementation, not a durable database or production logic.
// Money: integer minor YER units; quantities: integer thousandths.
class SaleCommand {
  final String id, company, branch, item, actor;
  final int quantityMilli, unitPriceMinor;
  const SaleCommand(this.id, this.company, this.branch, this.item,
      this.quantityMilli, this.unitPriceMinor, this.actor);
  String get fingerprint => [company, branch, item, actor, quantityMilli, unitPriceMinor].join('|');
}
class SaleResult {
  final String documentId;
  final int stockMilli;
  final List<(String, int, int)> lines;
  const SaleResult(this.documentId, this.stockMilli, this.lines);
}
class ProofLedger {
  int stockMilli = 10000, documentCount = 0, outboxCount = 0;
  final Map<String, (String, SaleResult)> outcomes = {};
  SaleResult sell(SaleCommand c, {bool failBeforeCommit = false}) {
    if (c.actor != 'cashier-A' || c.company != 'A' || c.branch != 'A1') {
      throw StateError('DENIED');
    }
    if (c.item != 'PROOF-001' || c.quantityMilli <= 0 || c.unitPriceMinor != 100) {
      throw ArgumentError('INVALID_COMMAND');
    }
    final previous = outcomes[c.id];
    if (previous != null) {
      if (previous.$1 != c.fingerprint) throw StateError('IDEMPOTENCY_CONFLICT');
      return previous.$2;
    }
    if (c.quantityMilli > stockMilli) throw StateError('INSUFFICIENT_STOCK');
    if (c.quantityMilli % 1000 != 0) throw StateError('QUANTITY_PRECISION');
    final units = c.quantityMilli ~/ 1000;
    final revenue = units * c.unitPriceMinor, cost = units * 60;
    final lines = <(String, int, int)>[
      ('cash', revenue, 0), ('revenue', 0, revenue),
      ('cogs', cost, 0), ('inventory', 0, cost)
    ];
    final debit = lines.fold<int>(0, (s, l) => s + l.$2);
    final credit = lines.fold<int>(0, (s, l) => s + l.$3);
    if (debit != credit) throw StateError('UNBALANCED');
    final result = SaleResult('DOC-${documentCount + 1}', stockMilli - c.quantityMilli, lines);
    if (failBeforeCommit) throw StateError('INJECTED_FAILURE');
    // In-memory reference transition only; no durable transaction or process-level concurrency.
    stockMilli = result.stockMilli;
    documentCount++;
    outboxCount++;
    outcomes[c.id] = (c.fingerprint, result);
    return result;
  }
}
void main() {
  final ledger = ProofLedger();
  final c = SaleCommand('cmd-A-1', 'A', 'A1', 'PROOF-001', 2000, 100, 'cashier-A');
  final result = ledger.sell(c);
  assert(result.stockMilli == 8000);
  assert(identical(ledger.sell(c), result));
  assert(ledger.documentCount == 1);
  bool rejects(void Function() f) {
    try { f(); return false; } catch (_) { return true; }
  }
  assert(rejects(() => ledger.sell(SaleCommand('cmd-A-1', 'A', 'A1', 'PROOF-001', 1000, 100, 'cashier-A'))));
  assert(rejects(() => ledger.sell(SaleCommand('x', 'B', 'B1', 'PROOF-001', 1000, 100, 'cashier-A'))));
  assert(rejects(() => ledger.sell(SaleCommand('failure', 'A', 'A1', 'PROOF-001', 1000, 100, 'cashier-A'), failBeforeCommit: true)));
  assert(ledger.stockMilli == 8000 && ledger.documentCount == 1);
  final last = ledger.sell(SaleCommand('last', 'A', 'A1', 'PROOF-001', 8000, 100, 'cashier-A'));
  assert(last.stockMilli == 0);
  assert(rejects(() => ledger.sell(SaleCommand('oversell', 'A', 'A1', 'PROOF-001', 1000, 100, 'cashier-A'))));
  print('Dart in-memory reference checks PASS; no SQLite transaction/reopen/platform proof');
}
