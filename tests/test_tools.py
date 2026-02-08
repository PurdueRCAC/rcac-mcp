# SPDX-FileCopyrightText: 2025 Purdue University
# SPDX-License-Identifier: MIT

"""Tests for rcac_mcp.tools.docs — MCP doc_search and doc_load tools."""

from __future__ import annotations

import os
from pathlib import Path
from unittest import mock

import pytest

from rcac_mcp.docs.database import DocsDatabase
from rcac_mcp.docs.indexer import DocsIndexer

from conftest import requires_submodule


# ---------------------------------------------------------------------------
# Query normalization
# ---------------------------------------------------------------------------

class TestNormalizeQuery:

    def test_plain_terms_become_or_prefix(self) -> None:
        from rcac_mcp.tools.docs import _normalize_query
        result = _normalize_query('ssh key config setup')
        assert result == 'ssh* OR key* OR config* OR setup*'

    def test_stopwords_stripped(self) -> None:
        from rcac_mcp.tools.docs import _normalize_query
        result = _normalize_query('how do I configure the ssh keys')
        assert 'how' not in result.lower().split()
        assert 'the' not in result.lower().split()
        assert 'ssh*' in result
        assert 'keys*' in result
        assert 'configure*' in result

    def test_explicit_or_passthrough(self) -> None:
        from rcac_mcp.tools.docs import _normalize_query
        query = 'conda OR anaconda'
        assert _normalize_query(query) == query

    def test_quoted_phrase_passthrough(self) -> None:
        from rcac_mcp.tools.docs import _normalize_query
        query = '"job array"'
        assert _normalize_query(query) == query

    def test_prefix_wildcard_passthrough(self) -> None:
        from rcac_mcp.tools.docs import _normalize_query
        query = 'contai*'
        assert _normalize_query(query) == query

    def test_and_passthrough(self) -> None:
        from rcac_mcp.tools.docs import _normalize_query
        query = 'scratch AND purge'
        assert _normalize_query(query) == query

    def test_single_term(self) -> None:
        from rcac_mcp.tools.docs import _normalize_query
        assert _normalize_query('conda') == 'conda*'

    def test_all_stopwords_returns_original(self) -> None:
        from rcac_mcp.tools.docs import _normalize_query
        query = 'how do I'
        assert _normalize_query(query) == query

    def test_single_char_terms_dropped(self) -> None:
        from rcac_mcp.tools.docs import _normalize_query
        result = _normalize_query('R conda environment')
        assert 'R*' not in result
        assert 'conda*' in result


# ---------------------------------------------------------------------------
# Tool registration
# ---------------------------------------------------------------------------

class TestToolRegistration:

    def test_doc_search_in_registry(self) -> None:
        from rcac_mcp.tools import TOOL_REGISTRY
        names = [t.name for t in TOOL_REGISTRY]
        assert 'doc_search' in names

    def test_doc_load_in_registry(self) -> None:
        from rcac_mcp.tools import TOOL_REGISTRY
        names = [t.name for t in TOOL_REGISTRY]
        assert 'doc_load' in names


# ---------------------------------------------------------------------------
# Missing database (graceful handling)
# ---------------------------------------------------------------------------

class TestMissingDatabase:

    def test_doc_search_no_db(self, tmp_path: Path) -> None:
        from rcac_mcp.tools.docs import doc_search
        no_db = str(tmp_path / 'nonexistent.db')
        with mock.patch.dict(os.environ, {'RCAC_DOCS_DB': no_db}):
            result = doc_search.fn('scratch purge')
        assert 'not available' in result
        assert '--index-docs' in result

    def test_doc_load_no_db(self, tmp_path: Path) -> None:
        from rcac_mcp.tools.docs import doc_load
        no_db = str(tmp_path / 'nonexistent.db')
        with mock.patch.dict(os.environ, {'RCAC_DOCS_DB': no_db}):
            result = doc_load.fn('userguides/gautschi/storage.md')
        assert 'not available' in result


# ---------------------------------------------------------------------------
# With a built database
# ---------------------------------------------------------------------------

@requires_submodule
class TestDocSearchWithData:

    @pytest.fixture(autouse=True)
    def _setup_db(self, built_db: str) -> None:
        self._env_patch = mock.patch.dict(os.environ, {'RCAC_DOCS_DB': built_db})
        self._env_patch.start()

    @pytest.fixture(autouse=True)
    def _teardown_db(self) -> None:
        yield
        self._env_patch.stop()

    def test_search_returns_results(self) -> None:
        from rcac_mcp.tools.docs import doc_search
        result = doc_search.fn('scratch purge policy')
        assert 'result(s)' in result
        assert 'Path:' in result

    def test_search_no_results(self) -> None:
        from rcac_mcp.tools.docs import doc_search
        result = doc_search.fn('xyznonexistentqueryterm')
        assert 'No documentation found' in result

    def test_search_category_filter(self) -> None:
        from rcac_mcp.tools.docs import doc_search
        result = doc_search.fn('conda', category='software')
        assert 'result(s)' in result

    def test_search_category_no_match(self) -> None:
        from rcac_mcp.tools.docs import doc_search
        result = doc_search.fn('conda', category='nonexistent_category')
        assert 'No documentation found' in result


@requires_submodule
class TestDocLoadWithData:

    @pytest.fixture(autouse=True)
    def _setup_db(self, built_db: str) -> None:
        self._env_patch = mock.patch.dict(os.environ, {'RCAC_DOCS_DB': built_db})
        self._env_patch.start()

    @pytest.fixture(autouse=True)
    def _teardown_db(self) -> None:
        yield
        self._env_patch.stop()

    def test_load_existing_document(self) -> None:
        from rcac_mcp.tools.docs import doc_load
        result = doc_load.fn('index.md')
        assert len(result) > 50  # non-trivial content

    def test_load_nonexistent_document(self) -> None:
        from rcac_mcp.tools.docs import doc_load
        result = doc_load.fn('does/not/exist.md')
        assert 'not found' in result.lower()
