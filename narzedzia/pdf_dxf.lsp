;;; PDFDXF - GstarCAD
;;; Dla kazdego pliku DXF w wybranym folderze tworzy JEDEN plik PDF
;;; ze wszystkimi arkuszami (w kolejnosci zakladek) i zapisuje go obok DXF:
;;;   1233.dxf  ->  1233.pdf
;;;
;;; Uzycie: APPLOAD -> pdf_dxf.lsp, potem polecenie PDFDXF
;;; (najlepiej z pustego, nowego rysunku - nie z jednego z drukowanych plikow).

(vl-load-com)

;; ===================== USTAWIENIA =====================
;; Dokladna nazwa drukarki PDF z okna "Drukuj" w GstarCAD:
(setq *pdfdxf-drukarka* "DWG To PDF.pc5")
;; Folder, w ktorym leza pdf_dxf.lsp i scal_pdf.py (ukosniki "/" i "/" na koncu):
(setq *pdfdxf-narzedzia* "C:/narzedzia/")
;; ======================================================

(defun pdfdxf:slash (s) (vl-string-translate "\\" "/" s))

;; Drukuje wszystkie arkusze aktualnego rysunku do folderu tmp
;; jako <nazwa>__01.pdf, <nazwa>__02.pdf ... (kolejnosc zakladek).
(defun pdfdxf:drukuj-arkusze (tmp / nazwa i plik)
  (setq nazwa (vl-filename-base (getvar "DWGNAME"))
        i     0)
  (foreach lay (layoutlist)
    (setq i    (1+ i)
          plik (strcat tmp nazwa "__" (if (< i 10) "0" "") (itoa i) ".pdf"))
    (if (findfile plik) (vl-file-delete plik))
    (command "_.-PLOT" "_N" lay "" *pdfdxf-drukarka* plik "_N" "_Y")
  )
  (princ)
)

(defun c:PDFDXF (/ wsk dir tmp pliki scr f)
  (setq wsk (getfiled "Wskaz dowolny plik DXF w folderze do wydruku"
                      (getvar "DWGPREFIX") "dxf" 0))
  (if wsk
    (progn
      (setq dir   (strcat (pdfdxf:slash (vl-filename-directory wsk)) "/")
            tmp   (strcat dir "_strony/")
            pliki (vl-sort (vl-directory-files dir "*.dxf" 1) '<)
            scr   (strcat tmp "pdfdxf.scr"))
      (vl-mkdir tmp)
      (setvar "FILEDIA" 0)
      (if (getvar "BACKGROUNDPLOT") (setvar "BACKGROUNDPLOT" 0)) ; drukuj na pierwszym planie

      ;; budowa skryptu: otworz -> wydrukuj arkusze -> zamknij bez zapisu
      (setq f (open scr "w"))
      (foreach p pliki
        (write-line (strcat "_.OPEN \"" dir p "\"") f)
        (write-line (strcat "(load \"" *pdfdxf-narzedzia* "pdf_dxf.lsp\")") f)
        (write-line (strcat "(pdfdxf:drukuj-arkusze \"" tmp "\")") f)
        (write-line "_.CLOSE" f)
        (write-line "_N" f)
      )
      ;; na koniec: scalenie stron w jeden PDF na plik DXF
      (write-line (strcat "(startapp \"py\" \"\\\"" *pdfdxf-narzedzia*
                          "scal_pdf.py\\\" \\\"" dir "\\\"\")") f)
      (write-line "FILEDIA 1" f)
      (write-line "" f)
      (close f)

      (princ (strcat "\nDrukuje " (itoa (length pliki)) " plikow z: " dir))
      (command "_.SCRIPT" scr)
    )
  )
  (princ)
)

(princ "\nWczytano PDFDXF - wpisz PDFDXF, aby wydrukowac folder DXF do PDF.")
(princ)
