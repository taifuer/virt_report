"""Keep accelerated GPU rendering and optional video integration distinct."""
import re
from pathlib import Path

import pytest

from virt_report import mechanisms
from virt_report.config import Config
from virt_report.render import render


SLUG = "virtio-gpu"
SOURCE = Path(__file__).parents[1] / "virt_report/content/articles" / f"{SLUG}.md"
DIAGRAMS = (
    "virtio-gpu-resources", "virtio-gpu-update-sequence",
    "virtio-gpu-render-stack", "virtio-gpu-render-submit",
    "virtio-gpu-video-decode", "virtio-gpu-video-encode",
)


def test_rendering_article_embeds_both_resource_and_execution_interactions():
    article = mechanisms.get_article(SLUG)
    assert article["topic"] == "device-virtualization"
    assert article["source"] == f"{SLUG}.md"
    assert article['execution_demo'] == 'virtio-gpu-rendering'
    assert article.get("presentation") != "walkthrough"
    assert mechanisms.get_article('virtio-gpu-execution') is None
    assert mechanisms.get_redirect('virtio-gpu-execution')['target'] == SLUG
    assert {item["project"] for item in article["baselines"]} == {
        "QEMU", "Linux", "Mesa", "virglrenderer",
    }
    assert all(re.fullmatch(r"[0-9a-f]{40}", item["commit"])
               for item in article["baselines"])
    page = render.render_mechanisms_html(Config(), SLUG)
    assert 'virtio-gpu：资源、渲染与编解码 - virt-report' in page
    assert 'data-execution-lab="virtio-gpu-rendering"' in page
    assert 'data-virtio-gpu-lab' in page
    assert 'assets/virtio-gpu-lab.js?v=' in page
    assert 'VIRTIO 1.2 CS01' in page
    assert 'href="#walkthrough"' in page
    assert page.index('id="video-support"') < page.index('data-execution-lab=')
    for section in article["sections"]:
        assert f'id="{section["id"]}"' in page
        assert f'href="#{section["id"]}"' in page
    catalogue = render.render_mechanisms_html(Config())
    assert catalogue.count(f'href="mechanisms/{SLUG}.html"') == 1
    assert 'href="mechanisms/virtio-gpu-execution.html"' not in catalogue
    assert 'href="mechanisms/virtio-gpu-rendering.html"' not in catalogue
    assert not SOURCE.with_name("virtio-gpu-rendering.md").exists()


def test_gpu_rendering_and_video_diagrams_are_offline_and_zoomable():
    page = render.render_mechanisms_html(Config(), SLUG)
    assert page.count('data-mechanism-view="diagram"') == len(DIAGRAMS)
    for name in DIAGRAMS:
        assert name in render.MECHANISM_DIAGRAM_NAMES
        assert f'data-diagram="{name}"' in page
        assert f'../assets/{name}.svg?v=' in page
    assert 'assets/mechanism-viewer.js?v=' in page
    assert 'language-diagram' not in page
    assert 'mermaid.min.js' not in page


def test_optional_video_requirements_precede_codec_walkthroughs():
    article = SOURCE.read_text()
    preface = article.split('## 共享基础')[0]
    assert "初始化没有启用该视频功能" in preface
    support = article.split('{#video-support}', 1)[1].split('## 一帧 H.264', 1)[0]
    for requirement in ("-Dvideo=true", "VIRGL_RENDERER_USE_VIDEO",
                        "get_drm_fd", "Mesa Gallium", "QEMU 10.2.0"):
        assert requirement in support
    assert "默认接入不能直接运行" in support
    assert all("virtio-gpu-gl" not in block for block in
               re.findall(r'```(?:bash|sh)\n(.*?)```', article, re.S))


@pytest.mark.parametrize("markers", [
    ("glDrawArrays", "virgl_draw_vbo", "virgl_encoder_draw_vbo",
     "DRM_IOCTL_VIRTGPU_EXECBUFFER", "VIRTIO_GPU_CMD_SUBMIT_3D"),
    ("BEGIN_FRAME", "DECODE_BITSTREAM", "END_FRAME", "vaSyncSurface",
     "glCopyTexSubImage2D"),
    ("ENCODE_BITSTREAM", "vrend_video_encode_completed",
     "virgl_video_get_feedback", "coded_size"),
])
def test_article_traces_render_decode_and_encode_commands(markers):
    article = SOURCE.read_text()
    for marker in markers:
        assert marker in article
    assert "不能按 OpenGL 调用次数推算 virtqueue 请求数" in article
    assert "共享句柄也不意味着整个视频链没有复制" in article


def test_video_examples_keep_surface_and_feedback_constraints():
    article = SOURCE.read_text()
    assert "选择两端支持的 NV12 输出表面" in article
    assert "该函数等待相关缓冲区可复用" in article
    assert "成功状态本身不能证明输出缓冲区足够大" in article
    assert "普通、不带 FENCE 的命令响应可以早于 GPU 执行完成" in article


def test_rendering_article_separates_resource_dependencies_from_frame_pipelining():
    article = SOURCE.read_text()
    assert "实际应用可以让多个批次同时在途" in article
    assert "下一帧准备、当前帧渲染和上一帧显示重叠" in article
    assert "渲染及显示操作各自已完成" in article
    assert "只能解除 F1 所覆盖的渲染依赖" in article


def test_links_connect_principle_walkthrough_and_passthrough_articles():
    page = render.render_mechanisms_html(Config(), SLUG)
    for related in ("gpu-execution",):
        assert f'href="{related}.html"' in page
        assert f'href="{SLUG}.html"' in render.render_mechanisms_html(Config(), related)
    assert "远程桌面的编码" in page
    assert "Vulkan VkQueue" in page


def test_merged_article_keeps_2d_and_3d_bookmarks_and_a_single_reading_path():
    article = mechanisms.get_article(SLUG)
    assert [section["id"] for section in article["sections"]] == [
        "preface", "resources", "update-flow", "render", "video-support",
        "walkthrough", "decode", "encode", "completion", "evolution", "sources",
    ]
    page = render.render_mechanisms_html(Config(), SLUG)
    for anchor in ("preface", "resources", "update-flow", "interactive", "commands",
                   "completion", "evolution", "summary", "references",
                   "commands-part-1", "commands-part-2", "update-flow-part-1",
                   "update-flow-part-2", "transport-part-1", "transport-part-2",
                   "render-part-1", "render-part-2", "render-part-3", "render-part-4"):
        assert page.count(f'id="{anchor}"') == 1
    assert SOURCE.read_text().count("```baselines") == 1
    assert page.index('id="interactive"') < page.index('id="render"')
