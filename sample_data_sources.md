# Sample Data Sources

Mục đích của file này là map các ô nguồn trong sơ đồ `Data Preparation` sang các nguồn thật đang có trong dự án.

## Link nguồn thật trong repo

- BEIR NFCorpus (Hugging Face dataset card): <https://huggingface.co/datasets/BeIR/nfcorpus>
- NFCorpus (trang chính thức Heidelberg): <https://www.cl.uni-heidelberg.de/statnlpgroup/nfcorpus/>
- PubMed: <https://pubmed.ncbi.nlm.nih.gov/>
- MedlinePlus Health Topics: <https://medlineplus.gov/healthtopics.html>
- NIH Health Information: <https://www.nih.gov/health-information>

## Mapping gợi ý từ hình sang nguồn thật

### Dataset A

- Gợi ý dùng: `MedlinePlus Health Topics`
- Link: <https://medlineplus.gov/healthtopics.html>
- Lý do: đây là nguồn website sức khỏe công cộng, phù hợp với kiểu crawl thủ công hoặc bán thủ công ở giai đoạn seed data.
- Trong repo:
  - `source_id`: `medlineplus`
  - `source_type`: `website`
  - `crawl_strategy`: `sitemap`
  - `update_frequency`: `weekly`
  - `enabled`: `true`

### Dataset B

- Gợi ý dùng: `NIH Health Information`
- Link: <https://www.nih.gov/health-information>
- Lý do: nguồn guideline/health info chính thống, phù hợp với ý tưởng cập nhật định kỳ hoặc crawl dựa trên chu kỳ cập nhật.
- Trong repo:
  - `source_id`: `nih_guidelines`
  - `source_type`: `guideline`
  - `crawl_strategy`: `catalog`
  - `update_frequency`: `monthly`
  - `enabled`: `true`

### Dataset B thay thế nếu bạn muốn nguồn paper y khoa

- Gợi ý dùng: `PubMed`
- Link: <https://pubmed.ncbi.nlm.nih.gov/>
- Lý do: nếu ô `Database B` trong sơ đồ của bạn muốn đại diện cho nguồn journal/paper y khoa thì PubMed là lựa chọn hợp lý hơn NIH.
- Trong repo:
  - `source_id`: `pubmed`
  - `source_type`: `journal`
  - `crawl_strategy`: `api`
  - `update_frequency`: `weekly`
  - `enabled`: `false`

### NFCorpus

- Link chính thức: <https://www.cl.uni-heidelberg.de/statnlpgroup/nfcorpus/>
- Link dataset card trong workflow hiện tại: <https://huggingface.co/datasets/BeIR/nfcorpus>
- Trong repo:
  - `source_id`: `beir_nfcorpus`
  - `source_type`: `benchmark`
  - `crawl_strategy`: `download`
  - `update_frequency`: `one-shot`
  - `enabled`: `true`

## Sample Markdown để chèn vào báo cáo / README

```md
## Data Preparation Sources

### Dataset A - MedlinePlus
- Nguồn: https://medlineplus.gov/healthtopics.html
- Loại nguồn: website
- Cách thu thập: sitemap / bán thủ công
- Tần suất cập nhật: weekly
- Ghi chú: dùng làm authoritative seed source

### Dataset B - NIH Health Information
- Nguồn: https://www.nih.gov/health-information
- Loại nguồn: guideline
- Cách thu thập: catalog / update-based crawling
- Tần suất cập nhật: monthly
- Ghi chú: dùng để mở rộng authoritative health information

### Benchmark - NFCorpus
- Nguồn chính thức: https://www.cl.uni-heidelberg.de/statnlpgroup/nfcorpus/
- Dataset card: https://huggingface.co/datasets/BeIR/nfcorpus
- Loại nguồn: benchmark
- Cách thu thập: download
- Tần suất cập nhật: one-shot
- Ghi chú: dùng để benchmark retrieval và answer generation
```

## File nguồn trong repo

- Registry gốc: [01-data-preparation/data/source_registry.csv](C:\Users\LENOVO\Desktop\Đồ Án Truy Hồi Thông Tin\01-data-preparation\data\source_registry.csv)
