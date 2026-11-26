import 'dart:io';

void writeProofStatus(String message) {
  if (const bool.fromEnvironment('BN_V3_WRITE_STATUS')) {
    File('runtime-result.tmp')
      ..writeAsStringSync(message, flush: true)
      ..renameSync('runtime-result.txt');
  }
}

Future<String> coordinateProof(Future<String> Function() run) => run();
