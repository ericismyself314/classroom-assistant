import {
  pipeline,
  env,
  type AutomaticSpeechRecognitionPipeline,
} from "@huggingface/transformers";

env.allowLocalModels = false;
// GitHub Pages cannot supply COOP/COEP headers; avoid shared-memory WASM threads.
if (env.backends.onnx.wasm) env.backends.onnx.wasm.numThreads = 1;
self.onmessage = async ({
  data,
}: MessageEvent<{ audio: Float32Array; language: string }>) => {
  let recognizer: AutomaticSpeechRecognitionPipeline | undefined;
  let response:
    { type: "complete"; result: unknown } | { type: "error"; message: string };
  try {
    recognizer = await pipeline<"automatic-speech-recognition">(
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
    response = { type: "complete", result };
  } catch (error) {
    response = {
      type: "error",
      message: error instanceof Error ? error.message : String(error),
    };
  } finally {
    // The UI terminates the worker on its terminal message. Release first so
    // success and inference failures both get a chance to clean up explicitly.
    try {
      await recognizer?.dispose();
    } catch (error) {
      // Cleanup failure must not hide a transcript or the original ASR error.
      console.warn("语音模型资源释放失败；主界面将终止 Worker。", error);
    }
  }
  self.postMessage(response);
};
