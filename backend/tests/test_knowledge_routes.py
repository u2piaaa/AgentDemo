from app.api.routes.knowledge import extract_mcp_resource_text


def test_extract_mcp_resource_text_reads_standard_contents_blocks() -> None:
    resource = {
        "contents": [
            {
                "uri": "skill://example/SKILL.md",
                "mimeType": "text/markdown",
                "text": "# Example skill\n\nInstructions",
            }
        ]
    }

    assert extract_mcp_resource_text(resource) == "# Example skill\n\nInstructions"


def test_extract_mcp_resource_text_reads_nested_content_blocks() -> None:
    resource = {"content": [{"type": "text", "text": "First"}, {"text": "Second"}]}

    assert extract_mcp_resource_text(resource) == "First\n\nSecond"


def test_extract_mcp_resource_text_ignores_blob_only_resources() -> None:
    resource = {"contents": [{"uri": "mcp://image", "blob": "AAAA"}]}

    assert extract_mcp_resource_text(resource) == ""
