import torch
from transformers import AutoModelForCausalLM, AutoTokenizer
from PIL import Image

if torch.backends.mps.is_available():
    device = torch.device("mps")
    print("Apple Silicon (MPS) を使用して高速推論します")
else:
    device = torch.device("cpu")
    print("CPUを使用します")

model_id = "vikhyatk/moondream2"
revision = "2025-01-09"
model = AutoModelForCausalLM.from_pretrained(
    model_id, trust_remote_code=True, revision=revision
)
model = model.to(device)

tokenizer = AutoTokenizer.from_pretrained(model_id, revision=revision)

image = Image.open("book_spines.jpg")
prompt = "Describe this image in detail."

answer = model.query(image, prompt)["answer"]
print("AIの回答:", answer)
