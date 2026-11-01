// The settings page's microphone, for recording wake-word takes: hands each block of samples to the page,
// which keeps the ones it's recording and shows their level. Nothing leaves this PC.
class MaryRecorder extends AudioWorkletProcessor {
  process(inputs) {
    const channel = inputs[0] && inputs[0][0];
    if (channel) this.port.postMessage(channel.slice(0));
    return true;
  }
}

registerProcessor("mary-recorder", MaryRecorder);
