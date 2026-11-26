package org.bnmahyoub.proof

/** Synthetic, isolated proof logic. Money is in integer minor units; quantity is thousandths. */
data class SaleCommand(
    val id: String,
    val company: String,
    val branch: String,
    val item: String,
    val quantityMilli: Long,
    val expectedUnitPriceMinor: Long,
    val actor: String
)
data class JournalLine(val account: String, val debit: Long, val credit: Long)
data class SaleResult(val documentId: String, val stockMilli: Long, val lines: List<JournalLine>)
data class ProofState(
    var stockMilli: Long = 10_000L,
    var documentCount: Int = 0,
    var outboxCount: Int = 0,
    var lines: List<JournalLine> = emptyList(),
    val outcomes: MutableMap<String, Pair<SaleCommand, SaleResult>> = mutableMapOf()
)
class ProofLedger(private val state: ProofState = ProofState()) {
    @Synchronized
    fun sell(command: SaleCommand, failBeforeCommit: Boolean = false): SaleResult {
        require(command.actor == "cashier-A" && command.company == "A" && command.branch == "A1") { "DENIED" }
        require(command.item == "PROOF-001" && command.quantityMilli > 0 && command.expectedUnitPriceMinor == 100L)
        val previous = state.outcomes[command.id]
        if (previous != null) {
            require(previous.first == command) { "IDEMPOTENCY_CONFLICT" }
            return previous.second
        }
        require(command.quantityMilli <= state.stockMilli) { "INSUFFICIENT_STOCK" }
        val revenue = roundedPosting(command.quantityMilli, command.expectedUnitPriceMinor)
        val cost = roundedPosting(command.quantityMilli, 60L)
        val lines = listOf(
            JournalLine("cash", revenue, 0L), JournalLine("revenue", 0L, revenue),
            JournalLine("cogs", cost, 0L), JournalLine("inventory", 0L, cost)
        )
        check(lines.sumOf { it.debit } == lines.sumOf { it.credit })
        val result = SaleResult("DOC-${state.documentCount + 1}", state.stockMilli - command.quantityMilli, lines)
        if (failBeforeCommit) error("INJECTED_FAILURE")
        // In-memory reference transition only; real database transaction is a later mandatory gate.
        state.stockMilli = result.stockMilli
        state.documentCount++
        state.outboxCount++
        state.lines = state.lines + lines
        state.outcomes[command.id] = command to result
        return result
    }
    fun snapshot(): ProofState = state
}
private fun roundedPosting(quantity: Long, price: Long): Long {
    require(quantity > 0 && price >= 0)
    return java.math.BigInteger.valueOf(quantity).multiply(java.math.BigInteger.valueOf(price))
        .add(java.math.BigInteger.valueOf(500)).divide(java.math.BigInteger.valueOf(1000)).longValueExact()
}
fun main() {
    for ((qty, expected) in listOf(1L to 0L, 5L to 1L, 125L to 13L, 499L to 50L, 500L to 50L, 999L to 100L)) {
        val result = ProofLedger().sell(SaleCommand("fraction", "A", "A1", "PROOF-001", qty, 100, "cashier-A"))
        check(result.lines[0].debit == expected)
        check(result.stockMilli == 10000L - qty)
        check(result.lines.sumOf { it.debit } == result.lines.sumOf { it.credit })
    }
    val ledger = ProofLedger()
    val c = SaleCommand("cmd-A-1", "A", "A1", "PROOF-001", 2_000, 100, "cashier-A")
    val r = ledger.sell(c)
    check(r.stockMilli == 8_000L)
    check(ledger.sell(c) == r)
    check(ledger.snapshot().documentCount == 1)
    try { ledger.sell(c.copy(quantityMilli = 1_000)) ; error("replay changed") } catch (_: IllegalArgumentException) {}
    try { ledger.sell(c.copy(id = "unauthorized", company = "B")) ; error("cross tenant accepted") } catch (_: IllegalArgumentException) {}
    try { ledger.sell(c.copy(id = "failure"), true) ; error("failure missing") } catch (_: IllegalStateException) {}
    check(ledger.snapshot().stockMilli == 8_000L && ledger.snapshot().documentCount == 1)
    val last = ledger.sell(c.copy(id = "last", quantityMilli = 8_000))
    check(last.stockMilli == 0L)
    try { ledger.sell(c.copy(id = "over", quantityMilli = 1_000)); error("oversold") } catch (_: IllegalArgumentException) {}
    println("Kotlin in-memory reference checks PASS; no SQLite transaction/reopen/platform proof")
}
