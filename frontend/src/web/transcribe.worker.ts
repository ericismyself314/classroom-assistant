import { pipeline, env } from "@huggingface/transformers";

env.allowLocalModels = false;
// GitHub Pages cannot supply COOP/COEP headers; avoid shared-memory WASM threads.
if (env.backends.onnx.wasm) env.backends.onnx.wasm.numThreads = 1;
self.onmessage = async ({
  data,
}: MessageEvent<{ audio: Float32Array; language: string }>) => {
  try {
    const recognizer = await pipeline(
      "automatic-speech-recognition",
      "Xenova/whisper-tiny",
      {
        device: "wasm",
        dtype: "q8",
        progress_callback: (progress) =>
          self.postMessage({
            type: "progress",
            message: "下载 / 加载语音模型",
            progress,
          }),
      },
    );
    self.postMessage({
      type: "progress",
      message: "正在本机识别，请保持此页面打开…",
    });
    const result = await recognizer(data.audio, {
      chunk_length_s: 30,
      stride_length_s: 5,
      return_timestamps: true,
      task: "transcribe",
      ...(data.language === "auto" ? {} : { language: data.language }),
    });
    self.postMessage({ type: "complete", result });
    await recognizer.dispose();
  } catch (error) {
    self.postMessage({
      type: "error",
      message: error instanceof Error ? error.message : String(error),
    });
  }
};
