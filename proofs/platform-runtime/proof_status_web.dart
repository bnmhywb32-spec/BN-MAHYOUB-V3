import 'dart:js_interop';

@JS('navigator.locks.request')
external JSPromise<JSString> _requestLock(JSString name, JSFunction run);

Future<String> coordinateProof(Future<String> Function() run) async {
  JSPromise<JSString> callback() => run().then((result) => result.toJS).toJS;
  final result = await _requestLock(
    'bn-v3-flutter-runtime-v1'.toJS,
    callback.toJS,
  ).toDart;
  return result.toDart;
}

void writeProofStatus(String message) {}
