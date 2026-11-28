# bge-reranker-v2-m3 → ONNX(fp32) → 动态量化int8
# 前置: pip install torch --index-url https://download.pytorch.org/whl/cpu; pip install "transformers>=4.45" onnx onnxruntime
# 权重源: ModelScope(models/bge-reranker-v2-m3/, 下载见 scripts 头注或 D:\Temp\deeprag_review\download_ms_reranker.py 思路)
# 产出: models/bge-reranker-v2-m3/model_quantized.onnx (~600MB, CPU推理 ~100-300ms/20对)
# 用法: python scripts/export_reranker_onnx.py
import sys
from pathlib import Path

if sys.platform == "win32":
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", line_buffering=True)

MODEL_DIR = Path(__file__).resolve().parent.parent / "models" / "bge-reranker-v2-m3"
FP32 = MODEL_DIR / "model.onnx"
INT8 = MODEL_DIR / "model_quantized.onnx"


def main():
    import torch
    from transformers import AutoModelForSequenceClassification, AutoTokenizer

    assert (MODEL_DIR / "model.safetensors").exists(), f"权重未下载: {MODEL_DIR}"
    tok = AutoTokenizer.from_pretrained(str(MODEL_DIR))
    model = AutoModelForSequenceClassification.from_pretrained(str(MODEL_DIR)).eval()
    print("model loaded:", model.config.architectures, "num_labels =", model.config.num_labels)

    if not FP32.exists():
        inputs = tok("查询", "待排序文档", return_tensors="pt")
        print("export fp32 onnx (opset 14, dynamic batch/seq)...")
        torch.onnx.export(
            model,
            (inputs["input_ids"], inputs["attention_mask"]),
            str(FP32),
            input_names=["input_ids", "attention_mask"],
            output_names=["logits"],
            dynamic_axes={"input_ids": {0: "batch", 1: "seq"},
                          "attention_mask": {0: "batch", 1: "seq"},
                          "logits": {0: "batch"}},
            opset_version=14,
            dynamo=False,  # transformers5+torch2.6: 显式走legacy导出路径
        )
        print("fp32:", FP32.stat().st_size // (1024 * 1024), "MB")

    if not INT8.exists():
        from onnxruntime.quantization import QuantType, quantize_dynamic

        print("dynamic quantize int8...")
        quantize_dynamic(str(FP32), str(INT8), weight_type=QuantType.QInt8)
        print("int8:", INT8.stat().st_size // (1024 * 1024), "MB")

    # 冒烟: 一对正例一对负例, sigmoid(logit)应明显分离
    import onnxruntime as ort
    import numpy as np

    sess = ort.InferenceSession(str(INT8 if INT8.exists() else FP32), providers=["CPUExecutionProvider"])
    pairs = [("中文分词在项目里用的什么库?", "jieba是Python中文分词库,基于前缀词典与动态规划实现精确模式切分。"),
             ("中文分词在项目里用的什么库?", "大熊猫是中国特有珍稀动物,以竹子为主食。")]
    for name, (q, d) in (("pos", pairs[0]), ("neg", pairs[1])):
        ins = tok([q], [d], return_tensors="np")  # transformers5: 单对返回list, 必须显式np
        feed = {"input_ids": ins["input_ids"].astype(np.int64), "attention_mask": ins["attention_mask"].astype(np.int64)}
        logit = sess.run(["logits"], feed)[0][0][0]
        sig = 1.0 / (1.0 + np.exp(-float(logit)))
        print(f"  {name}: logit={float(logit):.4f} sigmoid={sig:.4f}")
    print("EXPORT_OK")


if __name__ == "__main__":
    sys.exit(main())
