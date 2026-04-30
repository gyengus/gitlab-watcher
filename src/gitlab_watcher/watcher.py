                # 2. Skip if already handled via emoji, reply, or session cache
                SKIP_EMOJIS = ["eyes", "white_check_mark", "heavy_check_mark", "check", "ballot_box_with_check", "x", "no_entry"]
                has_emojis = any(e in note.award_emojis for e in SKIP_EMOJIS)
                is_handled_discussion = note.discussion_id in handled_discussions
                
                last_processed_note = self.state.load(project.project_id).last_processed_note_id or 0
                
                # DOUBLE CHECK: If no emojis and no reply seen yet
                if not has_emojis and not is_handled_discussion and note.id not in self._processed_notes:
                    refreshed_emojis = self.gitlab.get_note_emojis(project.project_id, mr.iid, note.id)
                    has_emojis = any(e in refreshed_emojis for e in SKIP_EMOJIS)

                is_skipped = has_emojis or is_handled_discussion or note.id in self._processed_notes or note.id <= last_processed_note
                if is_skipped:
                    continue
