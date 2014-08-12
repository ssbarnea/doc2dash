from __future__ import absolute_import, division, print_function

import errno
import logging
import os
import plistlib
import shutil
import sqlite3

import click
import pytest

from click.testing import CliRunner
from mock import MagicMock, patch

import doc2dash

from doc2dash import __main__ as main


log = logging.getLogger(__name__)


@pytest.fixture
def runner():
    return CliRunner()


class TestArguments(object):
    def test_fails_with_unknown_icon(self, runner, tmpdir, monkeypatch):
        """
        Fail if icon is not PNG.
        """
        p = tmpdir.mkdir("sub").join("bar.png")
        p.write("GIF89afoobarbaz")
        result = runner.invoke(main.main, [str(tmpdir), '-i', str(p)])

        assert result.output.endswith("' is not a valid PNG image.\n")
        assert 1 == result.exit_code

    def test_handles_unknown_doc_types(self, tmpdir, runner):
        """
        If docs are passed but are unknown, exit with EINVAL.
        """
        result = runner.invoke(main.main, [str(tmpdir.mkdir("foo"))])
        assert errno.EINVAL == result.exit_code

    def test_quiet_and_verbose_conflict(self, runner, tmpdir):
        """
        Ensure main() exits on -q + -v
        """
        result = runner.invoke(main.main,
                               [str(tmpdir.mkdir("foo")), '-q', '-v'])
        assert 1 == result.exit_code
        assert "makes no sense" in result.output


def test_normal_flow(monkeypatch, tmpdir, runner):
    """
    Integration test with a mocked out parser.
    """
    def fake_prepare(source, dest, name, index_page):
        os.mkdir(dest)
        db_conn = sqlite3.connect(':memory:')
        db_conn.row_factory = sqlite3.Row
        db_conn.execute(
            'CREATE TABLE searchIndex(id INTEGER PRIMARY KEY, name TEXT, '
            'type TEXT, path TEXT)'
        )
        return 'data', db_conn

    def yielder():
        yield 'testmethod', 'testpath', 'cm'

    monkeypatch.chdir(tmpdir)
    png_file = tmpdir.join("icon.png")
    png_file.write(main.PNG_HEADER, mode="wb")
    os.mkdir('foo')
    monkeypatch.setattr(main, 'prepare_docset', fake_prepare)
    dt = MagicMock(detect=lambda _: True)
    dt.name = 'testtype'
    dt.return_value = MagicMock(parse=yielder)
    monkeypatch.setattr(doc2dash.parsers, 'get_doctype', lambda _: dt)
    with patch("doc2dash.__main__.log.info") as info, \
            patch("os.system") as system:
        result = runner.invoke(
            main.main, ["foo", "-n", "bar", "-a", "-i", str(png_file)]
        )

    assert 0 == result.exit_code
    out = '\n'.join(call[0][0] for call in info.call_args_list) + '\n'
    assert out == ('''\
Converting ''' + click.style("testtype", bold=True) + '''\
 docs from "foo" to "./bar.docset".
Parsing HTML...
Added ''' + click.style("1", fg="green") + ''' index entries.
Adding table of contents meta data...
Adding to dash...
''')
    assert system.call_args[0] == ('open -a dash "./bar.docset"', )


class TestSetupPaths(object):
    def test_works(self, tmpdir):
        """
        Integration tests with fake paths.
        """
        foo_path = str(tmpdir.join('foo'))
        os.mkdir(foo_path)
        assert (
            (foo_path, str(tmpdir.join('foo.docset')), "foo")
            == main.setup_paths(
                foo_path, str(tmpdir), name=None, add_to_global=False,
                force=False
            )
        )
        abs_foo = os.path.abspath(foo_path)
        assert (
            (abs_foo, str(tmpdir.join('foo.docset')), "foo") ==
            main.setup_paths(
                abs_foo, str(tmpdir), name=None, add_to_global=False,
                force=False
            )
        )
        assert (
            (abs_foo, str(tmpdir.join('baz.docset')), "baz") ==
            main.setup_paths(
                abs_foo, str(tmpdir), name="baz", add_to_global=False,
                force=False
            )
        )

    def test_A_overrides_destination(self):
        """
        Passing A computes the destination and overrides an argument.
        """
        assert '~' not in main.DEFAULT_DOCSET_PATH  # resolved?
        assert (
            'foo', os.path.join(main.DEFAULT_DOCSET_PATH, 'foo.docset'), "foo"
            == main.setup_paths(
                source='doc2dash', name=None, destination='foobar',
                add_to_global=True, force=False
            )
        )

    def test_detects_existing_dest(self, tmpdir, monkeypatch):
        """
        Exit with EEXIST if the selected destination already exists.
        """
        monkeypatch.chdir(tmpdir)
        os.mkdir('foo')
        os.mkdir('foo.docset')
        with pytest.raises(SystemExit) as e:
            main.setup_paths(
                source='foo', force=False, name=None, destination=None,
                add_to_global=False
            )
        assert e.value.code == errno.EEXIST

        main.setup_paths(
            source='foo', force=True, name=None, destination=None,
            add_to_global=False
        )
        assert not os.path.lexists('foo.docset')

    def test_deducts_name_with_trailing_slash(self, tmpdir, monkeypatch):
        """
        If the source path ends with a /, the name is still correctly deducted.
        """
        monkeypatch.chdir(tmpdir)
        os.mkdir('foo')
        assert "foo" == main.setup_paths(
            source='foo/', force=False, name=None,
            destination=None, add_to_global=False)[0]

    def test_cleans_name(self, tmpdir):
        """
        If the name ends with .docset, remove it.
        """
        d = tmpdir.mkdir("foo")
        assert "baz" == main.setup_paths(
            source=str(d), force=False, name="baz.docset", destination="bar",
            add_to_global=False,
        )[2]


class TestPrepareDocset(object):
    def test_plist_creation(self, monkeypatch, tmpdir):
        """
        All arguments should be reflected in the plist.
        """
        monkeypatch.chdir(tmpdir)
        m_ct = MagicMock()
        monkeypatch.setattr(shutil, 'copytree', m_ct)
        os.mkdir('bar')
        main.prepare_docset(
            "some/path/foo", 'bar', name="foo", index_page=None
        )
        m_ct.assert_called_once_with(
            'some/path/foo',
            'bar/Contents/Resources/Documents',
        )
        assert os.path.isfile('bar/Contents/Resources/docSet.dsidx')
        p = plistlib.readPlist('bar/Contents/Info.plist')
        assert p == {
            'CFBundleIdentifier': 'foo',
            'CFBundleName': 'foo',
            'DocSetPlatformFamily': 'foo',
            'DashDocSetFamily': 'python',
            'isDashDocset': True,
        }
        with sqlite3.connect('bar/Contents/Resources/docSet.dsidx') as db_conn:
            cur = db_conn.cursor()
            # ensure table exists and is empty
            cur.execute('select count(1) from searchIndex')
            assert cur.fetchone()[0] == 0

    def test_with_index_page(self, monkeypatch, tmpdir):
        """
        If an index page is passed, it is added to the plist.
        """
        monkeypatch.chdir(tmpdir)
        m_ct = MagicMock()
        monkeypatch.setattr(shutil, 'copytree', m_ct)
        os.mkdir('bar')
        main.prepare_docset('some/path/foo', 'bar', name='foo',
                            index_page='foo.html')
        p = plistlib.readPlist('bar/Contents/Info.plist')
        assert p == {
            'CFBundleIdentifier': 'foo',
            'CFBundleName': 'foo',
            'DocSetPlatformFamily': 'foo',
            'DashDocSetFamily': 'python',
            'isDashDocset': True,
            'dashIndexFilePath': 'foo.html',
        }


class TestSetupLogging(object):
    @pytest.mark.parametrize(
        "verbose, quiet, expected", [
            (False, False, logging.INFO),
            (True, False, logging.DEBUG),
            (False, True, logging.ERROR),
        ]
    )
    def test_logging(self, verbose, quiet, expected):
        """
        Ensure verbosity options cause the correct log level.
        """
        assert main.determine_log_level(verbose, quiet) is expected

    def test_quiet_and_verbose(self):
        """
        Fail if both -q and -v are passed.
        """
        with pytest.raises(ValueError) as e:
            main.determine_log_level(verbose=True, quiet=True)
        assert "makes no sense" in e.value.args[0]
